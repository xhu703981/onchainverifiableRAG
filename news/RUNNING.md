# Run the news pipeline

Run these commands from the `news` folder. Set `NEWS_DATA` to the data folder first. The default is `/workspace/news-data`.

## Install

```
python3 -m venv "$NEWS_DATA/venv"
"$NEWS_DATA/venv/bin/pip" install -r requirements.lock
```

## Run one cycle

```
"$NEWS_DATA/venv/bin/python" -m newsreg.crawl
"$NEWS_DATA/venv/bin/python" -m newsreg.register
```

The register step needs `PRIVATE_KEY` in `../.env.arc`. It sends transactions to Arc testnet only.

## Run on a schedule

```
jobs/newsjobs.sh start
jobs/newsjobs.sh status
jobs/newsjobs.sh stop
```

The crawl runs about every hour. Registration runs every 8 hours. Logs go to `$NEWS_DATA/logs`.

## Verify a bundle

```
python ../webdemo/verify_citations.py examples/bundle_genuine.json
```

- To use your own trusted keys, add `--trust 0x...`.
- To accept only the outlets you chose, add `--outlets "BBC News" "NPR"`.

To verify in the browser, start `app.py` and open `http://127.0.0.1:8766`. The page sends its checks to the Arc RPC directly.

## Test

```
"$NEWS_DATA/venv/bin/python" -m pytest tests
cd .. && forge test
```
