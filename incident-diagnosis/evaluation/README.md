# Clean retrieval model-selection evaluation

Open [model_selection_colab.ipynb](model_selection_colab.ipynb) in Google Colab and run the cells in order. The notebook compares BM25-only retrieval with the clean agent's current MiniLM model and the pinned Mixedbread xsmall contender.

Choose a standard CPU runtime. That matches the clean agent's ONNX CPU execution path; a GPU is optional but is not required or measured by this benchmark.

The notebook clones this repository, downloads pinned local models from Hugging Face, and verifies each ONNX artifact checksum. No API key is needed. It does not contact Kubernetes, Redis, n8n, Slack, the diagnosis dashboard, or any production service.

The only inputs are the frozen current-schema files:

- `fixtures/retrieval_cases.json` — labelled incident evidence and expected retrieval behavior.
- `fixtures/knowledge_snapshot.json` — `families`, `examples`, and `hints` in the clean `KnowledgeCorpus` shape.

The notebook shows quality, safety, and operational charts. It does not use an LLM judge or a confusion matrix: this evaluates retrieval and abstention behavior, not single-label classification.
