"""跑全部测试：python tests/run_all.py"""

import importlib
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for _p in (str(HERE), str(ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

MODULES = ["test_patch", "test_mixin", "test_config", "test_scan"]


def main():
    failed = []
    for name in MODULES:
        module = importlib.import_module(name)
        try:
            module.main()
        except Exception:
            traceback.print_exc()
            failed.append(name)
    print("=" * 60)
    if failed:
        print(f"失败: {', '.join(failed)}")
        return 1
    print(f"全部通过（{len(MODULES)} 个测试模块）")
    return 0


if __name__ == "__main__":
    sys.exit(main())