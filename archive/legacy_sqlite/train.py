"""Train the decision model and save model.joblib (picked up by analyzer.py).

  python train.py --synthetic            # demo model from generated data
  python train.py --csv labeled.csv      # your real data

CSV columns: tilt_x,tilt_y,vibration_magnitude,vibration_detected,
             battery_voltage,battery_percentage,label   (label: 0 ok, 1 warning, 2 critical)

The synthetic model only imitates the threshold rules in analyzer.py, so it is
a pipeline check, not real intelligence. Export rows from data.db, label them,
and train with --csv for anything meaningful.
"""
import argparse

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report
from sklearn.model_selection import train_test_split

from analyzer import FEATURES, MODEL_PATH


def synthetic(n=6000, seed=0):
    rng = np.random.default_rng(seed)
    tx = rng.normal(0, 15, n); ty = rng.normal(0, 15, n)
    vib = np.abs(rng.normal(0.5, 1.0, n))
    vd = (vib > 1.2).astype(float)
    bv = rng.uniform(3.0, 4.2, n)
    bp = np.clip((bv - 3.0) / 1.2 * 100, 0, 100)
    X = np.column_stack([tx, ty, vib, vd, bv, bp])
    tilt = np.maximum(np.abs(tx), np.abs(ty))
    y = np.zeros(n, dtype=int)
    y[(tilt >= 30) | (vib >= 1.5)] = 1
    y[(tilt >= 45) | (vib >= 3.0)] = 2
    return X, y


def from_csv(path):
    data = np.genfromtxt(path, delimiter=",", names=True)
    X = np.column_stack([data[f] for f in FEATURES])
    return X, data["label"].astype(int)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--synthetic", action="store_true")
    g.add_argument("--csv")
    a = ap.parse_args()

    X, y = synthetic() if a.synthetic else from_csv(a.csv)
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.25, random_state=0,
                                          stratify=y)
    clf = RandomForestClassifier(n_estimators=150, random_state=0).fit(Xtr, ytr)
    print(classification_report(yte, clf.predict(Xte)))
    joblib.dump(clf, MODEL_PATH)
    print("saved", MODEL_PATH)
