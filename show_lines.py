with open("processing/audio_transcription.py", encoding="utf-8") as f:
    lines = f.readlines()
for i, line in enumerate(lines[160:200], start=161):
    print(f"{i:4} | {line.rstrip()}")
