"""Create local Compose credentials without overwriting existing configuration."""
from pathlib import Path
import secrets

if __name__ == '__main__':
    target = Path(__file__).resolve().parents[1] / '.env'
    with target.open('x', encoding='utf-8') as stream:
        stream.write(f'DATABASE_PASSWORD={secrets.token_hex(24)}\nADMIN_USER=admin\n'
                     f'ADMIN_PASSWORD={secrets.token_hex(24)}\nSERVER_NAME=Offwork Pet\n'
                     'CHAT_DAYS=30\nLISTEN_ADDRESS=127.0.0.1\nPORT=8765\n')
    target.chmod(0o600)
    print(f'Created {target}. Read ADMIN_PASSWORD locally; keep this file private.')
