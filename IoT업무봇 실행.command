#!/bin/zsh
# 더블클릭하면 IoT업무봇를 실행하고 브라우저를 연다. 이 창을 닫으면 IoT업무봇도 꺼진다.
cd "$(dirname "$0")" && exec /usr/bin/python3 -I hub.py
