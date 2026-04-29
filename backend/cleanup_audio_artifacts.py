import argparse
import json

from app.services.global_transfer_tools import cleanup_audio_artifacts


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean SkillHub audio and processed artifact storage.")
    parser.add_argument("--older-than-hours", type=float, default=0)
    parser.add_argument("--mode", choices=["soft", "hard"], default="hard")
    args = parser.parse_args()
    result = cleanup_audio_artifacts(older_than_hours=args.older_than_hours, mode=args.mode)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
