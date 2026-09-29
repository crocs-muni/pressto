# demo file for ocr and possible preprocessing, not very accurate, fuzzy hashing will be better - run on raspberry pi
import subprocess
import pytesseract
from PIL import Image
import time
import os
import cv2
import numpy as np
import matplotlib.pyplot as plt
IMG_PATH = "/tmp/capture.jpg"
ROI = "0.375,0.375,0.25,0.25"

def capture_image():
    subprocess.run(["rpicam-still", "-o", IMG_PATH, "-t", "1000", "--roi", ROI], check=True)

def read_text_from_image(img):
    plt.imshow(img)
    plt.show()
    text = pytesseract.image_to_string(img)
    return text
def preprocess_image(path):
    img = cv2.imread(path)
    img = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    thresh, im_bw = cv2.threshold(img, 127, 255, cv2.THRESH_BINARY)
    img = cv2.bitwise_not(img)
    kernel = np.ones((1,1), np.uint8)
    img = cv2.dilate(img,kernel, iterations=1)
    img = cv2.erode(img,kernel,iterations=1)
    img = cv2.morphologyEx(img, cv2.MORPH_CLOSE, kernel)
    img = cv2.medianBlur(img, 3)
    
    return img
    

if __name__ == "__main__":
    if os.path.exists(IMG_PATH):
        os.remove(IMG_PATH)

    capture_image()
    img = preprocess_image(IMG_PATH)
    text = read_text_from_image(img)
    print("Detected text:")
    print(text)

