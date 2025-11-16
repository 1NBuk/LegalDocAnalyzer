# LegalDocAnalyzer

Автоматический анализ русскоязычных юридических документов (PDF/DOCX/сканы) с использованием LLM и ML.

## Функции
- Загрузка и обработка PDF/DOCX документов
- Извлечение текста (OCR для сканов)
- LLM-анализ: тип документа, стороны, даты, суммы, краткое резюме, risk flags
- Сохранение результатов в JSON и CSV
- FastAPI API для демонстрации анализа

## Установка
```bash
git clone https://github.com/1NBuk/LegalDocAnalyzer.git
cd LegalDocAnalyzer
python -m venv venv
source venv/bin/activate   # Linux/Mac
venv\Scripts\activate      # Windows
pip install -r requirements.txt
