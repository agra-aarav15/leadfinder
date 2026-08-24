"""One command to rule them all:  python run.py"""
import argparse
import sys


def main():
    p = argparse.ArgumentParser(description="LeadFinder")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8787)
    args = p.parse_args()

    import uvicorn
    print(f"\n  ⚡ LeadFinder  ->  http://{args.host}:{args.port}\n"
          f"     dashboard : http://{args.host}:{args.port}\n"
          f"     demo site : http://{args.host}:{args.port}/demo\n")
    uvicorn.run("app.main:app", host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    sys.exit(main())
