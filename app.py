from fastapi import FastAPI, UploadFile, File
from pathlib import Path
import shutil, uuid
from text_extract import extract_text
from llm_extractor import extract_with_llm

app = FastAPI()
UPLOAD_DIR = Path("./data")
UPLOAD_DIR.mkdir(exist_ok=True)
RESULTS_DIR = Path("./results")
RESULTS_DIR.mkdir(exist_ok=True)


@app.post("/analyze")
async def analyze(file: UploadFile = File(...)):
    uid = str(uuid.uuid4())
    path = UPLOAD_DIR / f"{uid}_{file.filename}"
    with open(path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    text = extract_text(path)
    result = extract_with_llm(text)

    out_json = RESULTS_DIR / f"{uid}.json"
    with open(out_json, "w", encoding="utf-8") as f:
        import json
        json.dump({"filename": file.filename, "result": result}, f, ensure_ascii=False, indent=2)

    #return {"filename": file.filename, "result": result}
    return {"message": "API работает"}

