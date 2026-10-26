"""Опциональный ML-классификатор типа документа (TF-IDF + RandomForest).

В API не используется: нужен размеченный корпус. Пример обучения см. в README.
"""
from __future__ import annotations

import pickle
from typing import Sequence

from sklearn.ensemble import RandomForestClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import classification_report
from sklearn.model_selection import train_test_split


class DocumentClassifier:
    def __init__(self):
        self.vectorizer = TfidfVectorizer(max_features=5000)
        self.model = RandomForestClassifier(n_estimators=100, random_state=42, n_jobs=-1)

    def train(self, texts: Sequence[str], labels: Sequence[str]):
        X = self.vectorizer.fit_transform(texts)
        self.model.fit(X, labels)

    def evaluate(self, texts: Sequence[str], labels: Sequence[str], test_size: float = 0.2) -> str:
        """Обучает на train-части и возвращает precision/recall/F1 на отложенной выборке."""
        tr_x, te_x, tr_y, te_y = train_test_split(
            texts, labels, test_size=test_size, random_state=42, stratify=labels
        )
        self.train(tr_x, tr_y)
        return classification_report(te_y, self.predict(te_x), zero_division=0)

    def predict(self, texts: Sequence[str]):
        return self.model.predict(self.vectorizer.transform(texts))

    def save(self, path="ml_model.pkl"):
        with open(path, "wb") as f:
            pickle.dump((self.vectorizer, self.model), f)

    def load(self, path="ml_model.pkl"):
        """Внимание: pickle небезопасен — загружайте только собственные модели."""
        with open(path, "rb") as f:
            self.vectorizer, self.model = pickle.load(f)
