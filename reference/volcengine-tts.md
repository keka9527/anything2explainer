# 火山引擎 TTS 2.0

## 用途

中文长视频优先按自然段合成：同一自然段只调用一次火山 TTS，避免逐句重置音色、气口和情绪。音频完成后，再用本机 `faster-whisper` 对齐每个 `|` 字幕块。画面和字幕继续使用生成后的同一份时间轴。

## 一次性开通

1. 打开[火山引擎语音控制台](https://console.volcengine.com/speech/app)，创建或选择语音应用并开通需要的音色。
2. 打开 [API Key 页面](https://console.volcengine.com/speech/new/setting/apikeys)，创建 API Key。
3. 在视频工程根目录复制 `.env.example` 为 `.env`，只把真实 Key 填入本机 `.env`：

   ```dotenv
   TTS_ENGINE=volcengine
   VOLCENGINE_TTS_API_KEY=你的API_Key
   VOLCENGINE_TTS_SPEAKER=zh_female_vv_uranus_bigtts
   ```

`.env` 已被 Git 忽略。不要把 Key 写入 `SKILL.md`、命令、日志、截图或聊天。

## 生成

解说词中：一行是一句，`|` 只切字幕；空行表示自然段。火山引擎模式会把同一自然段的多句连贯合成，不会在每句之间硬塞静音。

```powershell
python scripts/tts_build.py script/narration.txt
```

首次对齐会加载本地 Whisper 模型。默认 `base`，已有更高精度模型时可在 `.env` 设置 `VOLCENGINE_ALIGN_MODEL=small`。

## 调时长

保持 `VOLCENGINE_TTS_SPEECH_RATE=0` 或接近 0。成片时长偏差超过 15% 时增删有效文案，不用大幅减速，也不要靠大量静音补齐。`PARAGRAPH_GAP` 默认只有 6 帧；章节空隙由 `CHAPTER_GAP` 控制。

## 接口依据

适配器使用官方 TTS 2.0 v3 SSE 端点和 `seed-tts-2.0` 资源标识，协议字段参考 ByteDance AgentKit 官方 Apache-2.0 示例。代码不会自动创建、打印或上传 API Key。
