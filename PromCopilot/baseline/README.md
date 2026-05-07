# baseline

## file description

1. `.env`: optional user API configuration. Leave it empty to use local Qwen-0.6B through `http://127.0.0.1:8000/v1`; set `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_BASE_URL`, and `LLM_MODEL` only when using your own API.
2. `config.py`: configure `TOP_N`, `CHAT_MODEL`, `EMBEDDING_MODEL`, etc.
3. `system.txt`: basic system prompt.
4. `utils.py`: functions for embedding questions, count length of PromQL, etc.
5. `result.py`: calculate the accuracy of PromQL generation.
6. `few-shot.py`: baseline logics of few-shot learning to generate PromQL.
7. `data/question.csv`: questions and PromQLs of testing dataset.
8. `data/question_embedding.csv`: embeddings of testing dataset (local hash embeddings by default).
9. `data/history.csv`: questions and PromQLs of history dataset (for few-shot learning).
10. `data/history_embedding.csv`: embeddings of history dataset (local hash embeddings by default).
11. `data/full.csv`: questions and PromQLs of full dataset (including history).
12. `data/count.csv`: distribution of full dataset (including history) PromQL length.
13. `result/{chat_model}/k={k}/k={k}_model={chat_model}_embedding={embedding_model}_labels.csv`: manually labeled results of PromQL generation when using `chat_model` for completion, `embedding_model` for embedding, and selecting top `k` similar history case(s) for few-shot learning.
14. `result/{chat_model}/k={k}/k={k}_model={chat_model}_embedding={embedding_model}.json`: detailed process records of PromQL generation when using `chat_model` for completion, `embedding_model` for embedding, and selecting top `k` similar history case(s) for few-shot learning.
