import time

last_copy_time = 0

def update_last_copy_time():
    global last_copy_time
    last_copy_time = time.time()

def get_last_copy_time():
    global last_copy_time
    return last_copy_time 