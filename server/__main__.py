import argparse
import uvicorn

parser = argparse.ArgumentParser(description='下班搭子互动服务（单进程）')
parser.add_argument('--host', default='127.0.0.1')
parser.add_argument('--port', type=int, default=8765)
args = parser.parse_args()
uvicorn.run('server.app:create_app', factory=True, host=args.host, port=args.port, workers=1,
            ws_max_size=16384, ws_ping_interval=10, ws_ping_timeout=20)
