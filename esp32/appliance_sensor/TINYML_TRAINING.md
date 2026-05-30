# Appliance Sensor — TinyML Training Guide

The default firmware uses threshold-based classification which works for 95%
of appliances. If your machine is unusual, train a proper ML model.

## 1. Data collection

First, temporarily add this to loop() to log raw features over Serial:

```cpp
Serial.printf("%.0f,%.0f,%s\n", vibSmoothed, audioSmoothed, currentState.c_str());
```

Run your appliance through a full cycle and capture the Serial output to a
CSV file. Collect at least:
- 5 minutes idle
- Full wash cycle (typically 45-90 min)
- Full spin cycle
- Full dry cycle (if dryer)

## 2. Train a model (Python)

```python
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report

df = pd.read_csv("appliance_log.csv", names=["vib_rms","audio_rms","label"])
X = df[["vib_rms","audio_rms"]]
y = df["label"]

X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2)
clf = RandomForestClassifier(n_estimators=10, max_depth=5)
clf.fit(X_train, y_train)
print(classification_report(y_test, clf.predict(X_test)))
```

## 3. Export to TFLite

```python
# Convert to a tiny TFLite model for ESP32
import tensorflow as tf
import numpy as np

# Wrap sklearn model predictions as training data for a tiny TF model
X_all = df[["vib_rms","audio_rms"]].values.astype(np.float32)
y_pred = clf.predict(X_all)
labels = {"idle":0, "running":1, "spinning":2, "done":3}
y_int = np.array([labels[l] for l in y_pred])

model = tf.keras.Sequential([
    tf.keras.layers.Dense(8, activation='relu', input_shape=(2,)),
    tf.keras.layers.Dense(4, activation='softmax')
])
model.compile(optimizer='adam', loss='sparse_categorical_crossentropy', metrics=['accuracy'])
model.fit(X_all, y_int, epochs=50, verbose=0)

converter = tf.lite.TFLiteConverter.from_keras_model(model)
converter.optimizations = [tf.lite.Optimize.DEFAULT]
tflite = converter.convert()
with open("appliance_model.tflite", "wb") as f: f.write(tflite)
print(f"Model size: {len(tflite)} bytes")
```

## 4. Deploy to ESP32

Use the EloquentTinyML library (Arduino Library Manager):
```cpp
#include <EloquentTinyML.h>
#include "appliance_model.h"   // convert .tflite with xxd -i

Eloquent::TinyML::TfLite<2, 4, 4096> ml;

void setup() {
    ml.begin(appliance_model_tflite);
}

// In classify():
float input[2] = {vibSmoothed, audioSmoothed};
float output[4];
ml.predict(input, output);
// output[0]=idle, [1]=running, [2]=spinning, [3]=done
int predicted = std::max_element(output, output+4) - output;
```

Typical model size: 2-4KB for a 2-feature, 4-class model. Fits easily in
ESP32 flash alongside the main firmware.
