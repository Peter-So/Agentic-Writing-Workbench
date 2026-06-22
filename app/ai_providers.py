PROVIDERS = [
    {
        "id": "qwen",
        "name": "千问",
        "url": "https://www.qianwen.com/chat/",
        "embed_status": "blocked",
        "reason": "X-Frame-Options SAMEORIGIN",
        "automation": "playwright",
    },
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "url": "https://chat.deepseek.com/",
        "embed_status": "blocked",
        "reason": "CSP frame-ancestors none",
        "automation": "playwright",
    },
    {
        "id": "doubao",
        "name": "豆包",
        "url": "https://www.doubao.com/chat/",
        "embed_status": "external",
        "reason": "跨域页面通过后端 Playwright 自动化桥接入",
        "automation": "playwright",
    },
]
