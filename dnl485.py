import serial
import time
import sys

import struct
import re
import string
import ihex

global ser

def grep_bl_message():
    global ser
    bl_msg = "bl_reps"
    start = time.time()
    msg_pos = 0
    c = b''
    while (time.time() - start) <= 5:
        c = ser.read(1)
        if len(c) == 1: 
            print(c)
            if bl_msg[msg_pos] == chr(c[0]):
                msg_pos = msg_pos + 1
                if msg_pos == len(bl_msg):
                    return True
            else:
                msg_pos = 0
    return False


def read_line():
    line = bytearray()
    start = time.time()
    msg_pos = 0
    c = b''
    while (time.time() - start) <= 10:
        c = ser.read(1)
        if len(c) > 0:
            if c == b'\x0A':
                return line
            line += c
    return line

    


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python dnl485.py [serial_port_device] [hex_file_to_download]")
        exit(0)
    serial_port=sys.argv[1]
    hex_file=sys.argv[2]
    ser = serial.Serial(serial_port, 115200)
    ser.timeout = 0.1
    # 1. wait for device_id
    while True:
        while ser.read(1):
            pass
        ser.flushInput()
        print("sending BCK")
        ser.write(b'\x08')
        if not grep_bl_message():
            time.sleep(1)
            continue
        device_id = read_line().decode("utf-8")
        print(device_id)
        if device_id.startswith("ADuC"):
            break
    # 2. read hex file

    # 3. erase pages

    # 4. program

    ser.close()


