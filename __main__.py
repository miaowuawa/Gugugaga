# -*- coding: utf-8 -*-
"""包入口：python -m qigumi_grabber（任意目录可运行）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qigumi_grabber.account import AccountManager
from qigumi_grabber.ui import main_menu


def main():
    mgr = AccountManager()
    main_menu(mgr)


if __name__ == "__main__":
    main()
