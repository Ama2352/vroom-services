from flask import Flask, request, jsonify
from rank_bm25 import BM25Okapi
import os, re

app = Flask(__name__)
DOCS_DIR = os.environ.get("DOCS_DIR", "/docs")


def tokenize(text):
    return [w for w in re.split(r'\W+', text.lower()) if w]


def load_docs():
    paragraphs = []
    for fname in sorted(os.listdir(DOCS_DIR)):
        if not fname.endswith(".md"):
            continue
        try:
            text = open(os.path.join(DOCS_DIR, fname)).read()
        except Exception:
            continue
        for para in re.split(r'\n{2,}', text):
            para = para.strip()
            if len(para) > 50:
                paragraphs.append({"source": fname, "text": para})
    return paragraphs


PARAGRAPHS = load_docs()
BM25 = BM25Okapi([tokenize(p["text"]) for p in PARAGRAPHS]) if PARAGRAPHS else None


@app.route("/search")
def search():
    q = request.args.get("q", "")
    if not q or BM25 is None:
        return jsonify({"result": "No runbook content available.", "source": ""})
    scores = BM25.get_scores(tokenize(q))
    best = PARAGRAPHS[int(scores.argmax())]
    return jsonify({"result": best["text"][:300].replace("\n", " "), "source": best["source"]})


@app.route("/health")
def health():
    return jsonify({"status": "ok", "paragraphs_indexed": len(PARAGRAPHS)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
