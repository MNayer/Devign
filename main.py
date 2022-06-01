import argparse
import os
import pickle
import sys
import json

import numpy as np
import torch
from torch.nn import BCELoss
from torch.optim import Adam

from data_loader.dataset import DataSet
from modules.model import DevignModel, GGNNSum
from trainer import train
from utils import tally_param, debug

CUDA="CUDA" in os.environ and os.environ["CUDA"] == "1"
print("CUDA is %s" % ("enabled" if CUDA else "disabled"))

def save_after_ggnn(output_dir, model, loss_function, name, num_batches, get_fn, logger_fn=print):
    all_predictions = []
    all_targets = []
    all_loss = []
    final = []
    with torch.no_grad():
        for l in range(num_batches):
            graph, targets = get_fn()
            if CUDA:
                graph.cuda(device='cuda:0')
                graph.graph = graph.graph.to('cuda:0')
                targets = targets.cuda()
                predictions = model(graph, cuda=True)
            else:
                predictions = model(graph, cuda=False)
            batch_loss = loss_function(predictions, targets)
            all_predictions.extend(
                predictions.ge(torch.ones_like(predictions) / 2)
                .detach().cpu().int().numpy().tolist())
            all_targets.extend(targets.detach().cpu().int().numpy().tolist())
            all_loss.append(batch_loss.detach().cpu().item())

            output = model.get_graph_embeddings(graph)
            final += list(zip(output.tolist(), [int(i) for i in targets.tolist()]))

    final = list(map(lambda f: {'graph_feature':f[0], 'target':f[1]}, final))
    dst_filepath = os.path.join(output_dir, name + '_GGNNinput_graph.json')
    logger_fn(f'Saving to {dst_filepath}')
    with open(dst_filepath, 'w') as f:
        f.write(json.dumps(final))

if __name__ == '__main__':
    torch.manual_seed(1000)
    np.random.seed(1000)
    parser = argparse.ArgumentParser()
    parser.add_argument('--model_type', type=str, help='Type of the model (devign/ggnn)',
                        choices=['devign', 'ggnn'], default='devign')
    parser.add_argument('--dataset', type=str, required=True, help='Name of the dataset for experiment.')
    parser.add_argument('--input_dir', type=str, required=True, help='Input Directory of the parser')
    parser.add_argument('--node_tag', type=str, help='Name of the node feature.', default='node_features')
    parser.add_argument('--graph_tag', type=str, help='Name of the graph feature.', default='graph')
    parser.add_argument('--label_tag', type=str, help='Name of the label feature.', default='target')

    parser.add_argument('--feature_size', type=int, help='Size of feature vector for each node', default=100)
    parser.add_argument('--graph_embed_size', type=int, help='Size of the Graph Embedding', default=200)
    parser.add_argument('--num_steps', type=int, help='Number of steps in GGNN', default=6)
    parser.add_argument('--batch_size', type=int, help='Batch Size for training', default=128)
    parser.add_argument('--save_after_ggnn', action='store_true')
    args = parser.parse_args()

    if args.feature_size > args.graph_embed_size:
        print('Warning!!! Graph Embed dimension should be at least equal to the feature dimension.\n'
              'Setting graph embedding size to feature size', file=sys.stderr)
        args.graph_embed_size = args.feature_size

    model_dir = os.path.join('models', args.dataset)
    if not os.path.exists(model_dir):
        os.makedirs(model_dir)
    input_dir = args.input_dir
    processed_data_path = os.path.join(input_dir, 'processed.bin')
    if False and os.path.exists(processed_data_path):
        debug('Reading already processed data from %s!' % processed_data_path)
        dataset = pickle.load(open(processed_data_path, 'rb'))
        debug(len(dataset.train_examples), len(dataset.valid_examples), len(dataset.test_examples))
    else:
        dataset = DataSet(train_src=os.path.join(input_dir, 'train_GGNNinput.json'),
                          valid_src=os.path.join(input_dir, 'valid_GGNNinput.json'),
                          test_src=os.path.join(input_dir, 'test_GGNNinput.json'),
                          batch_size=args.batch_size, n_ident=args.node_tag, g_ident=args.graph_tag,
                          l_ident=args.label_tag)
        file = open(processed_data_path, 'wb')
        pickle.dump(dataset, file)
        file.close()
    assert args.feature_size == dataset.feature_size, \
        'Dataset contains different feature vector than argument feature size. ' \
        'Either change the feature vector size in argument, or provide different dataset.'
    if args.model_type == 'ggnn':
        model = GGNNSum(input_dim=dataset.feature_size, output_dim=args.graph_embed_size,
                        num_steps=args.num_steps, max_edge_types=dataset.max_edge_type)
    else:
        model = DevignModel(input_dim=dataset.feature_size, output_dim=args.graph_embed_size,
                            num_steps=args.num_steps, max_edge_types=dataset.max_edge_type)

    debug('Total Parameters : %d' % tally_param(model))
    debug('#' * 100)
    if CUDA:
        model.cuda()
    loss_function = BCELoss(reduction='sum')
    optim = Adam(model.parameters(), lr=0.0001, weight_decay=0.001)
    #train(model=model, dataset=dataset, max_steps=1000000, dev_every=128,
    #      loss_function=loss_function, optimizer=optim,
    #      save_path=model_dir + '/GGNNSumModel', max_patience=100, log_every=None)
    train(model=model, dataset=dataset, max_steps=1000000, dev_every=128,
          loss_function=loss_function, optimizer=optim,
          save_path=model_dir + '/GGNNSumModel', max_patience=5, log_every=None)

    if args.save_after_ggnn:
        after_ggnn_dir = os.path.join(args.input_dir, 'after_ggnn')
        if not os.path.exists(after_ggnn_dir):
            os.makedirs(after_ggnn_dir, exist_ok=True)
        save_after_ggnn(after_ggnn_dir, model, loss_function, 'test', dataset.initialize_test_batch(), dataset.get_next_test_batch, logger_fn=print)
        save_after_ggnn(after_ggnn_dir, model, loss_function, 'valid', dataset.initialize_valid_batch(), dataset.get_next_valid_batch, logger_fn=print)
        save_after_ggnn(after_ggnn_dir, model, loss_function, 'train', dataset.initialize_train_batch(), dataset.get_next_train_batch, logger_fn=print)
