import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier
import pickle

class DocumentClassifier:
    def __init__(self):
        self.vectorizer = TfidfVectorizer(max_features=5000)
        self.model = RandomForestClassifier(n_estimators=100, random_state=42)

    def train(self, texts, labels):
        X = self.vectorizer.fit_transform(texts)
        self.model.fit(X, labels)

    def predict(self, texts):
        X = self.vectorizer.transform(texts)
        return self.model.predict(X)

    def save(self, path="ml_model.pkl"):
        with open(path, "wb") as f:
            pickle.dump((self.vectorizer, self.model), f)

    def load(self, path="ml_model.pkl"):
        with open(path, "rb") as f:
            self.vectorizer, self.model = pickle.load(f)
