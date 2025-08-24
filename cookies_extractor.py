import sqlite3
import os
import json
import shutil
import time

def extract_firefox_cookies(profile_path, output_file="cookies.json"):
    cookies_db = os.path.join(profile_path, "cookies.sqlite")
    tmp_db = "cookies_tmp.sqlite"

    shutil.copy2(cookies_db, tmp_db)

    conn = sqlite3.connect(tmp_db)
    cursor = conn.cursor()

    cursor.execute(
        "SELECT host, name, value, path, isSecure, expiry FROM moz_cookies "
        "WHERE host LIKE '%backpack.tf%' OR host LIKE '%steamcommunity.com%'"
    )

    cookies = []
    for host, name, value, path, isSecure, expiry in cursor.fetchall():
        # Firefox иногда даёт None или 0 → Playwrightу нужно -1
        if expiry is None or expiry <= 0:
            expiry = -1
        # В БД бывает хранится в миллисекундах → переводим в секунды
        elif expiry > 1e12:
            expiry = int(expiry / 1000)

        cookies.append({
            "name": name,
            "value": value,
            "domain": host,
            "path": path,
            "secure": bool(isSecure),
            "expires": expiry
        })

    conn.close()
    os.remove(tmp_db)

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(cookies, f, indent=2)

    print(f"[OK] Куки сохранены в {output_file}")


if __name__ == "__main__":
    profile_path = r"C:\Users\Kostu\AppData\Roaming\Mozilla\Firefox\Profiles\zoe6nshy.default-release"
    extract_firefox_cookies(profile_path)
