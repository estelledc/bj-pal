"""剧场化开场（v2 改 10）。

UI 顶部一段微信对话动画 + 标语转场。
不依赖 JS 框架，用 CSS animation。
"""

from __future__ import annotations

import streamlit as st


def render_hero(show: bool = True):
    """渲染开场 hero 区。show=False 时折叠。"""
    if not show:
        return
    css = """
    <style>
      .bjpal-hero {
        background: linear-gradient(135deg, #FAF6EE 0%, #EDE4D2 100%);
        border-radius: 16px;
        padding: 18px 24px;
        margin-bottom: 16px;
        border: 1px solid #D5C8B0;
        box-shadow: 0 2px 8px rgba(200,48,45,0.05);
      }
      .bjpal-hero h2 {
        margin: 0 0 6px 0;
        color: #C8302D;
        font-family: serif;
      }
      .bjpal-hero .tagline {
        font-size: 13px; color: #666; margin-bottom: 14px;
      }
      .bjpal-chat {
        background: #fff; border-radius: 8px; padding: 10px 14px;
        font-size: 13px; max-width: 380px;
        animation: bjpal-fadein 0.6s ease-in;
        margin: 6px 0;
      }
      .bjpal-chat-from-me {
        background: #C8302D; color: #fff; border-radius: 8px;
        padding: 10px 14px; font-size: 13px;
        max-width: 380px; margin-left: auto;
        animation: bjpal-fadein 0.6s ease-in;
      }
      .bjpal-chat .who { font-size: 11px; color: #999; margin-bottom: 3px; }
      @keyframes bjpal-fadein {
        from { opacity: 0; transform: translateY(8px); }
        to   { opacity: 1; transform: translateY(0); }
      }
    </style>
    """
    html = """
    <div class="bjpal-hero">
      <h2>BJ-Pal · 周末闲时活动规划</h2>
      <div class="tagline">周末半天，把事做完——不是搜索推荐能解决的</div>
      <div class="bjpal-chat-from-me">
        <div class="who" style="color:#fee;text-align:right">小明 (我)</div>
        今天下午带老婆和 5 岁娃出去玩，别离家太远，4 小时左右。老婆减脂，娃喜欢动物。
      </div>
      <div class="bjpal-chat">
        <div class="who">老婆</div>
        你看这样行不？
      </div>
      <div class="bjpal-chat">
        <div class="who">小明 (我)</div>
        哎我让 AI 给我们安排一下试试 ↓
      </div>
    </div>
    """
    st.markdown(css + html, unsafe_allow_html=True)
