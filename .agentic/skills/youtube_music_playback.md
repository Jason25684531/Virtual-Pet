name: youtube_music_playback
description: Search, verify, and control music playback on YouTube.
trigger: 播放音樂, 播歌, 暫停音樂, 繼續播放, 停止音樂, 調整音量, play music
behavior: music_idle
xp_reward: 8
required_tool: youtube_music_tool
priority: 100
capability: music
slow_tool: true
post_tool_response_policy: ack_only
ack_template: 我來幫你找《{song}》。
tool_policy_json: {"allowed_domains":["www.youtube.com","youtube.com","m.youtube.com"],"allowed_actions":["search_and_play","pause","resume","stop","set_volume","get_status"],"auto_execute":true,"defaults":{"action":"search_and_play","autoplay":true},"selection_policy":{"max_candidates":8,"exclude_shorts":true,"exclude_live":true,"exclude_playlists":true},"success_criteria":"playback_verified","follow_up":["pause","resume","stop","set_volume","get_status"],"priority":100}
