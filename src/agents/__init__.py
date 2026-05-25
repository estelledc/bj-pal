"""BJ-Pal agent layer：Planner / Replanner / PreferenceMirror。

设计：所有 agent 通过 llm_client 抽象访问 LLM；切换 LongCat / Mock /
Anthropic 只改 env 一行。
"""
