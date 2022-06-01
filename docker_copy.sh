#!/bin/bash

docker cp ../Devign $(docker ps | grep reveal | tr -s ' '  | cut -d' ' -f1):/home/user/
