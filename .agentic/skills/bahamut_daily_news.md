name: bahamut_daily_news
description: List today's Bahamut GNN gaming news and follow up with an article.
trigger: 巴哈新聞, GNN新聞, 今日遊戲新聞, 遊戲新聞, game news today
behavior: news_idle
xp_reward: 7
required_tool: web_article_tool
priority: 100
capability: news
tool_policy_json: {"allowed_domains":["gnn.gamer.com.tw"],"allowed_actions":["list_articles","get_article_detail","open_article"],"auto_execute":true,"defaults":{"action":"list_articles","limit":5,"url":"https://gnn.gamer.com.tw/rss.xml"},"follow_up":["get_article_detail","open_article"],"timezone":"Asia/Taipei","priority":100}
