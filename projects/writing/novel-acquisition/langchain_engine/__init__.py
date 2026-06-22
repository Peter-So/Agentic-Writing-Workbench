# LangChain Writing Engine for 五维资料库
# Sidecar architecture: ChromaDB + Embedding service via HTTP
#
# THREE interfaces available:
#
#   1. Simple API (engine.py): WritingEngine class with method calls
#      from langchain_engine.engine import WritingEngine
#
#   2. LCEL Chains v1 (lcel_chains.py): Basic LangChain-native chains
#      from langchain_engine.lcel_chains import review_chain
#
#   3. LCEL Chains v2 (chains_v2.py): RECOMMENDED — Full project integration
#      from langchain_engine.chains_v2 import (
#          prose_chain,       # 章节正文生成 (auto-loads outline/characters/settings)
#          review_chain,      # 五维度审查 (few-shot calibrated)
#          fix_chain,         # 材料驱动修复 (auto-retrieves replacements)
#          expansion_chain,   # 扩写
#          outline_chain,     # 大纲生成
#          beat_chain,        # Beat Sheet
#          character_chain,   # 人物档案
#          review_fix_loop,   # 审查→修复闭环 (pre_llm_review + model review)
#      )
#
#   Document loader (doc_loader.py):
#      from langchain_engine.doc_loader import (
#          extract_chapter_outline,    # 从大纲MD按章号截取
#          extract_character_profiles, # 从人物档案MD按角色名截取
#          load_chapter_context,       # 一键加载章节全部上下文
#          load_project_settings,      # 001设定/终极命题/世界观
#      )
#
# Integrated specs (via prompts_v2.py):
#   - prompts/00-06 (material-driven workflow specs)
#   - 双线叙事规则.md, 穿越设定.md, 校园风格写作技巧.md
#   - 001设定及问题.md (终极命题/世界观/穿越顺序)
#
# Integrated review gates (via chains_v2.review_fix_loop):
#   - Gate 1: pre_llm_review.py (1046 lines, 15+ deterministic scanners)
#   - Gate 2: DeepSeek 5-dimension model review (de_ai/literary/structure/character/outline)
#   - Gate 3: Material-driven auto-fix with five-dim retrieval
