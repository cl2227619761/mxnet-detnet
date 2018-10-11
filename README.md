# DetNet in mxnet

This repository tries to reproduce the result in [DetNet: A Backbone network for Object Detection](https://arxiv.org/pdf/1804.06215.pdf).

### set up environment

1.clone this repository into the directory.
```
git clone git https://github.com/BigDeviltjj/mxnet-detnet.git
```

2.download coco dataset into data directory.

3.run`sh init.sh`.

4.specific requirements can be obtained from error message when running the program.

### demo

will update soon.

### train the model

```
python train_end2end.py
```

### evaluate the model

```
python test.py
```
this part may has bug since training process hasn't finished


### TODO

* test part code

* multigpu support

* offer pretained model

