import pandas as pd


def calculate(top_n: int, chat_model: str, embedding_model: str):
    df = pd.read_csv(f"result/{chat_model}/k={top_n}/k={top_n}_model={chat_model}_embedding={embedding_model}_labels.csv")
    rows = len(df)
    syntax = df["syntax"].sum()
    metric = df["metric"].sum()
    promql = df["promql"].sum()
    result = df["result"].sum()
    print(f"k={top_n} model={chat_model} embedding={embedding_model}")
    print(f"├─ metric: {metric} / {rows} = {metric / rows}")
    print(f"├─ syntax: {syntax} / {rows} = {syntax / rows}")
    print(f"├─ promql: {promql} / {rows} = {promql / rows}")
    print(f"└─ result: {result} / {rows} = {result / rows}\n")


if __name__ == "__main__":
    from config import *

    calculate(TOP_N, CHAT_MODEL_RUN_NAME, EMBEDDING_MODEL)
