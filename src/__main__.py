"""支持 python -m src 转发到 cli。"""

from src.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
