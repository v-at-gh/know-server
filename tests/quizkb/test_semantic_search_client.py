import pytest
from quizkb.client import OllamaClient, EmbedRecord

class MockOllamaClient:
    def __init__(self, should_fail=False):
        self.should_fail = should_fail
        self.embed_calls = 0

    def embed(self, model, input, dimensions):
        self.embed_calls += 1
        if self.should_fail:
            raise Exception("Ollama connection error")
        # Return list of 128-dim vectors (zeros) for each input item
        vecs = [[0.1] * 128 for _ in input]
        return {"embeddings": vecs, "prompt_eval_count": 10 * len(input)}

def test_embed_batch_returns_128_dim_vectors(monkeypatch):
    client = OllamaClient()
    mock_client = MockOllamaClient()
    monkeypatch.setattr(client, "_client", mock_client)

    rec = client.embed_batch(["hello world", "test input"], dimensions=128)
    assert rec.error is None
    assert len(rec.embeddings) == 2
    assert len(rec.embeddings[0]) == 128
    assert rec.prompt_tokens == 20

def test_embed_batch_empty_inputs():
    client = OllamaClient()
    rec = client.embed_batch([])
    assert rec.error is None
    assert rec.embeddings == []
    assert rec.prompt_tokens == 0

def test_embed_batch_error_handling(monkeypatch):
    client = OllamaClient()
    mock_client = MockOllamaClient(should_fail=True)
    monkeypatch.setattr(client, "_client", mock_client)

    rec = client.embed_batch(["test"])
    assert rec.error is not None
    assert "Ollama connection error" in rec.error
    assert rec.embeddings == []

def test_embed_batch_chunking(monkeypatch):
    client = OllamaClient()
    mock_client = MockOllamaClient()
    monkeypatch.setattr(client, "_client", mock_client)

    inputs = [f"text {i}" for i in range(150)]
    rec = client.embed_batch(inputs, batch_size=64)
    assert rec.error is None
    assert len(rec.embeddings) == 150
    # 150 items with batch_size 64 -> ceil(150/64) = 3 calls (64, 64, 22)
    assert mock_client.embed_calls == 3
