"""Prompt templates for the SEO blog workflow generator (public edition).

The prompt bodies are not published. Each constant keeps only the ${...}
placeholders that the generated workflow fills in at run time.
"""

P1_PROMPT = '（公開版では、このプロンプトの本文を省略しています）\n${KEYWORD}'

P2_T1 = '（公開版では、このプロンプトの本文を省略しています）'

P2_T2 = '（公開版では、このプロンプトの本文を省略しています）\n${KEYWORD}\n${OPTION1}'

P2_RETRY_PREFIX = '（公開版では、このプロンプトの本文を省略しています）'

P3_RAW = '（公開版では、このプロンプトの本文を省略しています）\n${KEYWORD}\n${SITENAME}\n${AUTHOR}\n${BASEURL}\n${FIRSTPERSON}\n${P1_KNOWLEDGE}'

P4_RAW = '（公開版では、このプロンプトの本文を省略しています）\n${SITENAME}\n${AUTHOR}\n${KEYWORD}\n${FIRSTPERSON}\n${P1_KNOWLEDGE}'

P4_REPAIR_RAW = '（公開版では、このプロンプトの本文を省略しています）\n${SITENAME}\n${AUTHOR}\n${P4_FEEDBACK}\n${FIRSTPERSON}\n${P1_KNOWLEDGE}'

P5_RAW = '（公開版では、このプロンプトの本文を省略しています）\n${KEYWORD}'

P6_ANALYZE_PROMPT = '（公開版では、このプロンプトの本文を省略しています）'

P6_IMAGE_PROMPT = '（公開版では、このプロンプトの本文を省略しています）'

P6_IMAGE_PROMPT_OPENAI = '（公開版では、このプロンプトの本文を省略しています）'

P6_IMAGE_PROMPT_OPENAI_NO_REFERENCE = '（公開版では、このプロンプトの本文を省略しています）'

P6_IMAGE_PROMPT_NO_REFERENCE = '（公開版では、このプロンプトの本文を省略しています）'
