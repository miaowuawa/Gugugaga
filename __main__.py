# -*- coding: utf-8 -*-
"""程序入口，同时分派主菜单和独立的抢票进度窗口。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def main():
    # 冻结版会用 ``--grab-window`` 重新启动自身，以便在新的控制台显示
    # 抢票进度。这个分派也让源码版和 exe 版使用完全相同的运行路径。
    if len(sys.argv) > 1 and sys.argv[1] == "--grab-window":
        from qigumi_grabber.grab_window import main as grab_window_main
        sys.argv.pop(1)
        grab_window_main()
        return

    from qigumi_grabber.account import AccountManager
    from qigumi_grabber.ui import main_menu
    mgr = AccountManager()
    main_menu(mgr)


if __name__ == "__main__":
    main()
