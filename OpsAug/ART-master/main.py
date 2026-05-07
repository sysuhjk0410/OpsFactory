"""ART 主入口：使用 art_tools 端到端流水线。"""
import warnings
warnings.filterwarnings("ignore")

from art_tools import run_art_pipeline

if __name__ == "__main__":
    run_art_pipeline(
        dataset="D1",
        workflow=["AD", "FT", "RCL"],
    )
