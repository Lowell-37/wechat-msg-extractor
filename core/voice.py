"""语音消息提取与转写。

管线: MSG.Buf (BLOB) → SILK 解码 → PCM → WAV → Whisper API → 文本
"""

import io
import struct
import wave
from datetime import datetime
from typing import Dict, List

import httpx
import pysilk

from config import VoiceConfig
from core.dbutils import MergedMsgDB


class VoiceTranscriber:
    """语音消息提取与转写器。"""

    # WeChat voice Buf 格式：第1字节为类型标记，后续为 SILK 数据
    BUF_HEADER_SIZE = 1
    SAMPLE_RATE = 16000

    def __init__(self, ddb: MergedMsgDB, config: VoiceConfig):
        self._ddb = ddb
        self._config = config

    # ─── 数据提取 ───────────────────────────────────────────

    def fetch_voice_messages(
        self, chat_id: str, start_ts: int, end_ts: int
    ) -> List[dict]:
        """查询 Type=34 语音消息，返回 [{msg_id, timestamp, buf}, ...]"""
        rows = self._ddb.execute_all(
            "SELECT localId, CreateTime, Buf FROM MSG "
            "WHERE StrTalker=? AND CreateTime BETWEEN ? AND ? AND Type=34 "
            "ORDER BY CreateTime ASC",
            (chat_id, start_ts, end_ts),
        )
        result = []
        for r in rows:
            buf = r["Buf"]
            if buf and len(buf) > self.BUF_HEADER_SIZE:
                result.append({
                    "msg_id": r["localId"],
                    "timestamp": r["CreateTime"],
                    "buf": buf,
                })
        return result

    # ─── SILK 解码 ──────────────────────────────────────────

    def decode_silk(self, buf: bytes) -> bytes:
        """解码 WeChat 语音 Buf → PCM bytes (16kHz, mono, 16bit)。

        WeChat 的 Buf 第1字节是格式标记，之后是 SILK v3 数据。
        """
        silk_data = buf[self.BUF_HEADER_SIZE:]  # 跳过第1字节
        with io.BytesIO(silk_data) as inp, io.BytesIO() as out:
            pysilk.decode(inp, out, sample_rate=self.SAMPLE_RATE)
            return out.getvalue()

    # ─── PCM → WAV 封装 ────────────────────────────────────

    @staticmethod
    def pcm_to_wav(pcm: bytes, sample_rate: int = 16000) -> bytes:
        """将 PCM 16-bit mono 封装为 WAV 格式。"""
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(sample_rate)
            wf.writeframes(pcm)
        return buf.getvalue()

    # ─── Whisper API 转写 ───────────────────────────────────

    async def transcribe(self, wav_bytes: bytes) -> str:
        """调用 Whisper API 转写一段 WAV 音频。"""
        if not self._config.api_key:
            return ""

        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            resp = await client.post(
                f"{self._config.api_base.rstrip('/')}/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {self._config.api_key}"},
                files={"file": ("voice.wav", wav_bytes, "audio/wav")},
                data={"model": self._config.model, "language": "zh"},
            )
            resp.raise_for_status()
            return resp.json().get("text", "").strip()

    # ─── 主入口 ─────────────────────────────────────────────

    async def transcribe_all(
        self, chat_id: str, start_ts: int, end_ts: int
    ) -> Dict[str, List[str]]:
        """提取并转写所有语音消息，按日期分组。

        返回: {date.isoformat(): [text, ...]}
        """
        voice_msgs = self.fetch_voice_messages(chat_id, start_ts, end_ts)
        if not voice_msgs:
            return {}

        result: Dict[str, List[str]] = {}
        for vm in voice_msgs:
            try:
                pcm = self.decode_silk(vm["buf"])
                wav = self.pcm_to_wav(pcm, self.SAMPLE_RATE)
                text = await self.transcribe(wav)
                if text:
                    dt = datetime.fromtimestamp(vm["timestamp"]).date().isoformat()
                    result.setdefault(dt, []).append(text)
            except Exception:
                pass  # 单条失败不影响整体

        return result
