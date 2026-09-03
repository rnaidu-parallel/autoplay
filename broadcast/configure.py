"""Seed a private portable OBS profile; existing local settings are preserved."""
import json
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "local/obs-studio/config/obs-studio"
PROFILE = CONFIG / "basic/profiles/Autoplay"

def create(path, data):
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) if isinstance(data, dict) else data, encoding="utf-8", newline="\n")

create(CONFIG / "global.ini", "[General]\nLicenseAccepted=true\n")
create(CONFIG / "user.ini", "[General]\nFirstRun=true\n\n[Basic]\nProfile=Autoplay\nProfileDir=Autoplay\nSceneCollection=Autoplay\nSceneCollectionFile=Autoplay\nConfigOnNewProfile=false\n\n[BasicWindow]\nPreviewEnabled=true\n")
create(CONFIG / "plugin_config/obs-websocket/config.json", {
    "first_load": False, "server_enabled": True, "server_port": 4455,
    "alerts_enabled": False, "auth_required": True, "server_password": secrets.token_urlsafe(32),
})
recordings = ROOT / "local/recordings"
recordings.mkdir(parents=True, exist_ok=True)
create(PROFILE / "basic.ini", f"""[General]
Name=Autoplay

[Video]
BaseCX=1920
BaseCY=1080
OutputCX=1920
OutputCY=1080
FPSType=0
FPSCommon=30
ColorFormat=NV12
ColorSpace=709
ColorRange=Partial

[Audio]
SampleRate=48000
ChannelSetup=Stereo

[Output]
Mode=Advanced

[AdvOut]
Encoder=obs_nvenc_h264_tex
AudioEncoder=ffmpeg_aac
TrackIndex=1
Track1Bitrate=160
ApplyServiceSettings=false
RecType=Standard
RecFilePath={recordings.as_posix()}
RecFormat2=mkv
RecEncoder=none
RecTracks=1
RecSplitFile=false
""")
create(PROFILE / "streamEncoder.json", {"rate_control":"CBR", "bitrate":6000, "keyint_sec":2,
                                       "preset":"p5", "profile":"high", "bf":2, "lookahead":False, "multipass":"disabled"})
create(PROFILE / "service.json", {"type":"rtmp_common", "settings":{"service":"Twitch", "server":"auto", "key":""}})
create(PROFILE / "obs-multi-rtmp.json", {"targets":[
    {"id":name.lower(), "name":name, "protocol":"RTMP", "sync-start":True, "sync-stop":True,
     "service-param":{"server":"", "key":""}, "output-param":{}}
    for name in ("Kick", "YouTube")
], "video_configs":[], "audio_configs":[]})
create(CONFIG / "basic/scenes/Autoplay.json", {"name":"Autoplay", "current_scene":"Autoplay", "current_program_scene":"Autoplay",
       "scene_order":[{"name":"Autoplay"}], "sources":[{"name":"Autoplay", "id":"scene", "settings":{"items":[]}}]})
print("Portable Autoplay profile prepared; stream keys remain local and empty until connected.")
