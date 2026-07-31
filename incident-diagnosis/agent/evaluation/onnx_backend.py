from pathlib import Path

from evaluation.model_artifacts import ModelSpec


class OnnxCrossEncoder:
    def __init__(self, model_dir: Path, spec: ModelSpec):
        from transformers import AutoTokenizer
        import onnxruntime

        self.spec = spec
        self.tokenizer = AutoTokenizer.from_pretrained(
            model_dir, local_files_only=True, revision=spec.revision,
        )
        self.session = onnxruntime.InferenceSession(
            str(Path(model_dir) / spec.onnx_file),
            providers=["CPUExecutionProvider"],
        )
        self.input_names = {item.name for item in self.session.get_inputs()}

    def score(self, query: str, documents: tuple[str, ...]) -> tuple[float, ...]:
        if not documents:
            return ()
        import numpy

        encoded = self.tokenizer(
            [query] * len(documents), list(documents), padding=True, truncation=True,
            max_length=self.spec.max_length, return_tensors="np",
        )
        feeds = {name: value for name, value in encoded.items() if name in self.input_names}
        logits = self.session.run(None, feeds)[0]
        scores = tuple(float(value) for value in numpy.asarray(logits).reshape(-1))
        if len(scores) != len(documents):
            raise ValueError("ONNX output count does not match document count")
        return scores
