from flask import Flask, request, jsonify
import os, re

app = Flask(__name__)
DOCS_DIR = os.environ.get("DOCS_DIR", "/docs")


def load_docs():
    paragraphs = []
    for fname in os.listdir(DOCS_DIR):
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


def score(text, query_words):
    t = text.lower()
    return sum(t.count(w) for w in query_words)


PARAGRAPHS = load_docs()


@app.route("/search")
def search():
    q = request.args.get("q", "").lower()
    words = [w for w in re.split(r'\W+', q) if len(w) > 3]
    if not words or not PARAGRAPHS:
        return jsonify({"result": "No runbook content available.", "source": ""})
    scored = sorted([(score(p["text"], words), p) for p in PARAGRAPHS], reverse=True)
    best = scored[0][1]
    return jsonify({"result": best["text"][:300].replace("\n", " "), "source": best["source"]})


@app.route("/health")
def health():
    return jsonify({"status": "ok", "paragraphs_indexed": len(PARAGRAPHS)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
