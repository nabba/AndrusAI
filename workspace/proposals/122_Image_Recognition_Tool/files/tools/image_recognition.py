import cv2
import tensorflow as tf

def recognize_image(image_path):
    # Load the image
    image = cv2.imread(image_path)
    # Load the pre-trained model
    model = tf.keras.models.load_model('pretrained_model.h5')
    # Preprocess the image
    image = cv2.resize(image, (224, 224))
    image = image.reshape((1, 224, 224, 3))
    # Predict the class
    prediction = model.predict(image)
    return prediction