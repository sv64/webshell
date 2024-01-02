import asyncio
import os
import pty
import fcntl
import tty, termios
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import json, struct

app = FastAPI()


class PTYHandler:
    def __init__(self):
        self.master_fd, self.slave_fd = pty.openpty()
        self.shell_pid = os.fork()
        if self.shell_pid == 0:  # child
            os.close(self.master_fd)
            os.setsid()
            # tty.setcbreak(self.slave_fd) 
            tty.setraw(self.slave_fd, when=tty.TCSANOW)
            os.dup2(self.slave_fd, 0)
            os.dup2(self.slave_fd, 1)
            os.dup2(self.slave_fd, 2)
            os.close(self.slave_fd)
            # cmd = ["/bin/bash"]
            cmd = ["./shell"]
            os.execvp(cmd[0], cmd)
        else:  # parent
            os.close(self.slave_fd)
            tty.setraw(self.master_fd, when=tty.TCSANOW)
            # tty.setcbreak(self.master_fd) 
            flags = fcntl.fcntl(self.master_fd, fcntl.F_GETFL)
            fcntl.fcntl(self.master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    async def proxy_websocket(self, websocket: WebSocket):
        read_task = None
        write_task = None
        try:
            await websocket.accept()
            read_task = asyncio.create_task(self.read_from_master(websocket))
            write_task = asyncio.create_task(self.write_to_master(websocket))
            await asyncio.gather(read_task, write_task)
        except asyncio.CancelledError:
            pass  # Task cancellation should not be treated as an error
        finally:
            if read_task: read_task.cancel()
            if write_task: write_task.cancel()

    async def read_from_master(self, websocket: WebSocket):
        try:
            while True:
                await asyncio.sleep(0.01)  # Wait 10ms to prevent busy loop while checking for data
                try:
                    data = os.read(self.master_fd, 1024)
                    if data:
                        await websocket.send_bytes(data)
                except BlockingIOError:
                    # There is no data available to read; move on
                    pass
        except WebSocketDisconnect:
            pass

    async def write_to_master(self, websocket: WebSocket):
        try:
            while True:
                message = await websocket.receive()
                print(message)
                if "bytes" in message:
                    os.write(self.master_fd, message["bytes"])
                elif "text" in message:
                    if message["text"].startswith('{'):
                        # Handle the JSON message that sets the terminal size
                        resize_message = json.loads(message["text"])
                        if resize_message["action"] == "resize":
                            cols = resize_message["cols"]
                            rows = resize_message["rows"]
                            self.resize_pty(cols, rows)
                    else:
                        # This part is for regular text input
                        os.write(self.master_fd, message["text"].encode('utf-8'))

        except WebSocketDisconnect:
            pass

    def resize_pty(self, cols, rows):
        # Set the terminal size of the PTY
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)

pty_handler = PTYHandler()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await pty_handler.proxy_websocket(websocket)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=38000)
