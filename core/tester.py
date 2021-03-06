import pickle
import os
import time
import mxnet as mx
import numpy as np

from utils import image
from bbox.bbox_transform import bbox_pred, clip_boxes
from nms.nms import py_nms


class Predictor(object):
    def __init__(self, symbol, data_names, label_names, 
                 context = mx.cpu(), max_data_shapes = None,
                 provide_data = None ,provide_label = None,
                 arg_params = None, aux_params = None):
        self._mod = mx.mod.Module(symbol,data_names,context = context)
        self._mod.bind(provide_data, provide_label, for_training = False)
        self._mod.init_params(arg_params = arg_params, aux_params = aux_params)
    def predict(self, data_batch):
        self._mod.forward(data_batch)
        return [dict(zip(self._mod.output_names,_)) for _ in zip(*self._mod.get_outputs(merge_multi_context = False)))]

def im_detect(predictor, data_batch, data_names, scales, cfg):
    output_all = predictor.predict(data_batch)
    data_dict_all = [dict(data_names, data_batch.data)]
    scores_all = []
    pred_boxes_all = []
    for output, data_dict, scale in zip(output_all, data_dict_all,scales):
        rois = output['rois_output'].asnumpy()[:,1:]

        im_shape = data_dict['data'].im_shape
        scores = output['cls_prob_reshape_output'].asnumpy()[0]
        bbox_deltas = output['bbox_pred_reshape_output'].asnumpy()[0]
        pred_boxes = bbox_pred(rois,bbox_deltas)
        pred_boxes = clip_boxes(pred_boxes, im_shape[-2:])
        pred_boxes = pred_boxes / scale
        scores_all.append(scores)
        pred_boxes_all.append(pred_boxes)
    return scores_all, pred_boxes_all, data_dict_all
def detect_at_single_scale(predictor, data_names, imdb, test_data, cfg, thresh, vis, all_boxes_single_scale, logger):
    idx = 0
    data_time, net_time, post_time = 0.,0.,0.
    t = time.time()

    for im_info, data_batch in test_data:
        t1 = time.time() - t
        t = time.time()
        scales = im_info[0,2]
        scores_all, boxes_all, data_dict_all = im_detect(predictor, data_batch, data_names, [scales], cfg)
        
        t2 = time.time() - t
        t = time.time()

        for delta, (scores, boxes, data_dict) in enumerate(zip(scores_all, boxes_all,data_dict_all)):
            for j in range(1,imdb.num_classes):
                indexes = np.where(scores:,j] > thresh)[0]
                cls_scores = scores[indexes, j, np.newaxis]
                cls_boxes = boxes[indexes, j * 4: (j+1) * 4]
                cls_dets = np.hstack((cls_boxes, cls_scores)).copy()
                all_boxes_single_scale[j][idx+delta] = cls_dets
            if vis:
                boxes_this_image = [[]] + [all_boxes_single_scale[j][idx+delta] for j in range(1,imdb.num_classes)]
                data_for_vis = data_dict['data'].asnumpy().copy()
                vis_all_detection(data_for_vis,boxes_this_image, imdb.num_classes, scales, cfg)
        
        idx += test_data.batch_size
        t3 = time.time() - t
        t = time.time()
        data_time += t1
        net_time += t2
        post_time += t3

        print 'testing {}/{} with scale {}: data {:.4f}s net {:.4f}s post {:.4f}s' \
            .format(idx, imdb.num_images, cfg.SCALES, data_time / idx * test_data.batch_size,
                    net_time / idx * test_data.batch_size, post_time / idx * test_data.batch_size)

        if logger:
            logger.info('testing {}/{} with scale {}: data {:.4f}s net {:.4f}s post {:.4f}s'
                        .format(idx, imdb.num_images, cfg.SCALES, data_time / idx * test_data.batch_size,
                                net_time / idx * test_data.batch_size, post_time / idx * test_data.batch_size))



def pred_eval(predictor, test_data, imdb, cfg, vis = False, thresh = 1e-3, logger = None, ignore_cache = True):
    det_file = os.path.join(imdb.result_path, imdb.name + '_detections.pkl')

    if os.path.exists(det_file) and not ignore_cache:
        with open(det_file, 'rb') as fid:
            all_boxes = pickle.load(fid)
        info_str = imdb.evaluate_detections(all_boxes)
        if logger:
            logger.info('evaluate detections: \n{}'.format(info_str))

        return 
    assert vis or not test_data.shuffle
    data_names = [k[0] for k in test_data.provide_data]

    if not isinstance(test_data,mx.io.PrefetchingIter):
        test_data = mx.io.PrefetchingIter(test_data)

    max_per_image = cfg.TEST.max_per_image
    num_images = imdb.num_images

    for test_scale_index, test_scale in enumerate(cfg.TEST_SCALES):
        det_file_single_scale = os.path.join(imdb.result_path, imdb.name + "_detections_" + str(test_scale_index) + '.pkl')
        cfg.SCALES = [test_scale]
        test_data.reset()

        all_boxes_single_scale = [[[] for _ in range(num_images)]
                                  for _ in range(imdb.num_classes)]

        detect_at_single_scale(predictor, data_names, imdb, test_data, cfg, thresh, vis, all_boxes_single_scale, logger)

        with open(det_file_single_scale,'wb') as f:
            pickle.dump(all_boxes_single_scale, f, protocol = pickle.HIGHEST_PROTOCOL)

    all_boxes = [[[] for _ in range(num_images)] for _ in range(imdb.num_classes)]

    for test_scale_index, test_scale in enumerate(cfg.TEST_SCALES):
        det_file_single_scale = os.path.join(imdb.result_path, imdb.name + '_detections_' + str(test_scale_index) + '.pkl')
        if os.path.exists(det_file_single_scale):
            with open(det_file_single_scale, 'rb') as fid:
                all_boxes_single_scale = pickle.load(fid)
            for idx_class in range(1, imdb.num_classes):
                for idx_im in range(0,num_images):
                    if len(all_boxes[idx_class][idx_im]) == 0:
                        all_boxes[idx_class][idx_im] = all_boxes_single_scale[idx_class][idx_im]

                    else:
                        all_boxes[idx_class][idx_im] = np.vstack((all_boxes[idx_class][idx_im], all_boxes_single_scale[idx_class][idx_im]))

    for idx_class in range(1, imdb.num_classes):
        for idx_im in range(0, num_images):
            keep = py_nms(all_boxes[idx_class][idx_im],cfg.TEST.NMS)
            all_boxes[idx_class][idx_im] = all_boxes[idx_class][idx_im][keep, :]

    if max_per_image > 0:
        for idx_im in range(0,num_images):
            image_scores = np.hstack([all_boxes[j][idx_im][:,-1]
                                      for j in range(1, imdb.num_classes)])
            if len(image_scores) > max_per_image:
                image_thresh = np.sort(image_scores)[-max_per_image]
                for j in range(1, imdb.num_classes):
                    keep = np.where(all_boxes[j][idx_im][:,-1] >= image_thresh)[0]
                    all_boxes[j][idx_im] = all_boxes[j][idx_im][keep,:]
        
    with open(det_fie, 'wb') as f:
        pickle.dump(all_boxes, f, protocol = pickle.HIGHEST_PROTOCOL)

    info_str = imdb.evaluate_detections(all_boxes)
    if logger:
        logger.info("evaluate detections: \n{}".format(info_str))


def vis_all_detection(im_array, detections, class_names, scale, cfg, threshold = 1e-3):
    import matplotlib.pyplot as plt
    import random
    im = image.transform_inverse(im_array, cfg.network.PIXEL_MEANS)
    plt.imshow(im)
    for j, name in enumerate(class_names):
        if name == '__background__':
            continue
        color = (random.random(), random.random(),random.random())
        dets = detections[j]
        for det in dets:
            bbox = det[:4] * scale
            score = det[-1]
            if score < threshold:
                continue
            rec = plt.Rectangle((bbox[0],bbox[1]),bbox[2]-bbox[0],bbox[3]-bbox[1],fill = False,
                                edgecolor = color, linewidth = 3.5)
            plt.gca().add_patch(rect)
            plt.gca().text(bbox[0],bbox[1]-2,
                           '{:s} {:.3f}'.format(name, score),
                           bbox = dict(facecolor = color, alpha = 0.5), fontsize = 12, color = 'white')
    plt.show()    