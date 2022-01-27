#Getting greater Indicies
#https://www.geeksforgeeks.org/python-indices-of-numbers-greater-than-k/
#Zero-pad OpenCV
#https://linuxtut.com/en/540d3be3e570cbca644e/
#Applying mAP
#https://ctkim.tistory.com/79
#Use AP
#https://scikit-learn.org/stable/modules/generated/sklearn.metrics.average_precision_score.html
#Show PR Curve
#https://scikit-learn.org/stable/modules/generated/sklearn.metrics.PrecisionRecallDisplay.html
#Evaluation of COCO dataset
#https://github.com/cocodataset/cocoapi/blob/master/PythonAPI/pycocotools/cocoeval.py


from re import A
from cv2 import threshold
from numpy import average
import tensorflow as tf
physical_devices = tf.config.experimental.list_physical_devices('GPU')
if len(physical_devices) > 0:
    tf.config.experimental.set_memory_growth(physical_devices[0], True)
from absl import app, flags, logging
from absl.flags import FLAGS
import core.utils as utils
from core.yolov4 import filter_boxes
from tensorflow.python.saved_model import tag_constants
from PIL import Image
import cv2
import numpy as np
from tensorflow.compat.v1 import ConfigProto
from tensorflow.compat.v1 import InteractiveSession
import os
from os.path import isfile, join
import random
from sklearn.metrics import average_precision_score
import matplotlib.pyplot as plt
from sklearn.metrics import precision_recall_curve, PrecisionRecallDisplay
import json

flags.DEFINE_string('weights', './checkpoints/yolov4-416',
                    'path to weights file')
flags.DEFINE_integer('size', 416, 'resize images to')
flags.DEFINE_boolean('tiny', False, 'yolo or yolo-tiny')
flags.DEFINE_string('model', 'yolov4', 'yolov3 or yolov4')
flags.DEFINE_string('output', 'result.png', 'path to output image')
flags.DEFINE_float('iou', 0.45, 'iou threshold')
flags.DEFINE_float('score', 0.25, 'score threshold')

target_class_names = ['person', 'dog', 'cat']
target_yolo_ids = [0, 16, 15]
target_coco_ids = [1, 18, 17]
target_ap_infos = {}

def createFolder(directory):
    if not os.path.exists(directory):
        os.makedirs(directory)

def get_intersection_area(box1, box2):
    left = box1[0] if box1[0] > box2[0] else box2[0]
    right = box1[2] if box1[2] < box2[2] else box2[2]
    up = box1[1] if box1[1] > box2[1] else box2[1]
    down = box1[3] if box1[3] < box2[3] else box2[3]
    return (right - left) * (down - up)

def get_area(box):
    return (box[2]-box[0]) * (box[3]-box[1])

def get_iou(box1, box2):
    box1_area = get_area(box1)
    box2_area = get_area(box2)
    common_area = get_intersection_area(box1, box2)
    return common_area / (box1_area + box2_area - common_area)

def get_inference_results(original_image, saved_model_loaded):
    image_data = cv2.resize(original_image, (416, 416))
    image_data = image_data / 255.

    images_data = []
    images_data.append(image_data)
    images_data = np.asarray(images_data).astype(np.float32)
    batch_data = tf.constant(images_data)

    infer = saved_model_loaded.signatures['serving_default']
    return infer(batch_data)

def get_nms_result(pred_bbox):
    for key, value in pred_bbox.items():
        boxes = value[:, :, 0:4]
        pred_conf = value[:, :, 4:]

    return tf.image.combined_non_max_suppression(
        boxes=tf.reshape(boxes, (tf.shape(boxes)[0], -1, 1, 4)),
        scores=tf.reshape(
            pred_conf, (tf.shape(pred_conf)[0], -1, tf.shape(pred_conf)[-1])),
        max_output_size_per_class=50,
        max_total_size=50,
        iou_threshold=FLAGS.iou,
        score_threshold=FLAGS.score
    )

def visualize_pr_curve(y_trues, y_scores):
    precision, recall, _ = precision_recall_curve(y_trues, y_scores)
    disp = PrecisionRecallDisplay(precision, recall)
    disp.plot()
    plt.show()

def save_inference_result(boxes, scores, classes, valid_detections, original_image, file_name):
    pred_bbox = [boxes.numpy(), scores.numpy(), classes.numpy(), valid_detections.numpy()]
    image = utils.draw_bbox(original_image, pred_bbox)
    image = Image.fromarray(image.astype(np.uint8))
    image = cv2.cvtColor(np.array(image), cv2.COLOR_BGR2RGB)
    createFolder('./ap')
    cv2.imwrite('./ap/'+file_name, image)

def get_index(num, nums):
    for i in range(len(nums)):
        if num == nums[i]:
            return i
    print("get_index error")
    return -1

def add_to_ap_infos(target_name, info):
    if target_name not in target_ap_infos:
        target_ap_infos[target_name] = [info]
    else:
        target_ap_infos[target_name].append(info)

def convert_coco_box(gt_box, dims):
    height_pixels = dims[0]
    width_pixels = dims[1]
    x, y, width, height = gt_box
    return [y / height_pixels, x / width_pixels, (y+height) / height_pixels, (x+width) / width_pixels]

def get_ap_infos(boxes, scores, classes, gt_tuples, file_name, dims):
    boxes_f = boxes.numpy()[0]
    scores_f = scores.numpy()[0]
    classes_f = classes.numpy()[0]

    for idx in range(0, len(scores_f)):
        if scores_f[idx] > 0 and classes_f[idx] in target_yolo_ids:
            yolo_id = classes_f[idx]
            class_idx = get_index(yolo_id, target_yolo_ids)
            coco_id = target_coco_ids[class_idx]
            box = boxes_f[idx]
            gt_found = False
            for gt_tuple in gt_tuples[:]:
                gt_box = convert_coco_box(gt_tuple[1], dims)
                if gt_tuple[0] == coco_id and get_iou(box, gt_box) > 0.5:
                    # True Positive
                    target_name = target_class_names[class_idx]
                    add_to_ap_infos(target_name, [1, scores_f[idx]])
                    #print('TP :: ', file_name, ' ', target_name, ' ', 1, ' ', scores_f[idx])
                    gt_found = True
                    gt_tuples.remove(gt_tuple)
                    break
            if not gt_found:
                # False Positive
                target_name = target_class_names[class_idx]
                add_to_ap_infos(target_name, [0, scores_f[idx]])
                print('FP :: ', file_name, ' ', target_name, ' ', 0, ' ', scores_f[idx])
        elif scores_f[idx] > 0:
            box = boxes_f[idx]
            for gt_tuple in gt_tuples[:]:
                gt_box = convert_coco_box(gt_tuple[1], dims)
                if get_iou(box, gt_box) > 0.5:
                    coco_id = gt_tuples[0]
                    if coco_id in target_coco_ids:
                        # False Negative
                        # Misdetect target as another class
                        class_idx = get_index(coco_id, target_coco_ids)
                        target_name = target_class_names[class_idx]
                        add_to_ap_infos(target_name, [0, scores_f[idx]])
                        print('FN-Mis :: ', file_name, ' ', target_name, ' ', 0, ' ', scores_f[idx])
    # Failed to detect target.
    for gt_tuple in gt_tuples[:]:
        coco_id = gt_tuples[0]
        if coco_id in target_coco_ids:
            # False Negative
            # Did Not detect the target.
            class_idx = get_index(coco_id, target_coco_ids)
            target_name = target_class_names[class_idx]
            add_to_ap_infos(target_name, [0, scores_f[idx]])
            print('FN-Non :: ', file_name, ' ', target_name, ' ', 0, ' ', scores_f[idx])
            gt_tuples.remove(gt_tuple)

def execute_ap(target_num, img_dir, file_names, saved_model_loaded, img_seg_dict):
    endIdx = len(file_names)-1
    for i in range(target_num):
        rand_idx = random.randint(0, endIdx)
        file_name = file_names[rand_idx]
        #file_name = file_names[i]

        original_image = cv2.imread(img_dir+file_name)
        original_image = cv2.cvtColor(original_image, cv2.COLOR_BGR2RGB)
        dims = original_image.shape
        pred_bbox = get_inference_results(original_image, saved_model_loaded)
        boxes, scores, classes, valid_detections = get_nms_result(pred_bbox)
        if file_name in img_seg_dict:
            gt_tuples = img_seg_dict[file_name]
            get_ap_infos(boxes, scores, classes, gt_tuples, file_name, dims)
            save_inference_result(boxes, scores, classes, valid_detections, original_image, file_name)
        if i % 100 == 0:
            print(i,'-th')

    aps = []
    for class_type in target_ap_infos:
        class_info = target_ap_infos[class_type]
        class_info = np.array(class_info)
        y_true = class_info[:,0]
        y_scores = class_info[:,1]
        visualize_pr_curve(y_true, y_scores)
        ap = average_precision_score(y_true, y_scores)
        aps.append(ap)
        print(class_type,' ',ap,' sample num (',len(y_true),')')
    return sum(aps) / len(aps)

def main(_argv):
    config = ConfigProto()
    config.gpu_options.allow_growth = True
    session = InteractiveSession(config=config)
    STRIDES, ANCHORS, NUM_CLASS, XYSCALE = utils.load_config(FLAGS)
    saved_model_loaded = tf.saved_model.load(FLAGS.weights, tags=[tag_constants.SERVING])
    input_size = FLAGS.size
    
    src_dir = '../val2017/'
    
    with open('./image-seginfo.json', 'r') as img_seg:
        img_seg_dict = json.load(img_seg)

    img_dir = src_dir
    file_names = [f for f in os.listdir(img_dir) if isfile(join(img_dir, f))]
    target_num = 500
    map = execute_ap(target_num, img_dir, file_names, saved_model_loaded, img_seg_dict)
    print('map: ',map)
    
if __name__ == '__main__':
    try:
        app.run(main)
    except SystemExit:
        pass
