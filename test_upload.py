import asyncio
import httpx
async def main():
    with open("test.txt", "w") as f:
        f.write("hello")
    async with httpx.AsyncClient() as client:
        with open("test.txt", "rb") as f:
            resp = await client.post("https://tmpfiles.org/api/v1/upload", files={"file": f})
        print(resp.json())
asyncio.run(main())
