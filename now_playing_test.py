import requests
import time
import re
from io import BytesIO

import asyncio
from typing import Callable, Optional


def extract_title(metadata):
    """Extract StreamTitle from raw ICY metadata string"""
    import re
    title_match = re.search(r"StreamTitle='([^';]+)", metadata)
    if title_match:
        title = title_match.group(1)
        return title.strip()
    return None



class NowPlayingExtractHandler():
    def __init__(self,
                 source_url:str, 
                 interval:float = 1.0,
                 coro:Optional[Callable]=None):
        self.source_url = source_url
        self.interval = interval

        self.coro = coro

        self._last_metadata = ""
        self.last_title = ""

        self._stop_event = asyncio.Event()
        self._is_running = False

        self._task: asyncio.Task = None

        self._session = None


    def _read_metadata(self, verbose = False):
        metadata = ""

        try:
            response = self._session.get(self.source_url, stream=True)
            response.raise_for_status()
            
            metaint = None
            content_type = response.headers.get('content-type', '')

            # Parse Icecast/Shoutcast metaint from headers
            metaint_header = response.headers.get('icy-metaint')
            if metaint_header:
                metaint = int(metaint_header)
                if verbose:
                    print(f"Metadata interval: {metaint} bytes [web:10]")
            else:
                if verbose:
                    print("No ICY metadata found in headers")
                return
            
            if 'aac' in content_type.lower():
                if verbose:
                    print("AAC stream detected - using ICY metadata parsing")
            
            bytes_read = 0

            data = response.raw.read(metaint - bytes_read)      

            if not data:
                return metadata

            bytes_read += len(data)

            if bytes_read >= metaint:
                # Read metadata length byte
                metadata_len_byte = response.raw.read(1)
                if metadata_len_byte:
                    metadata_len = ord(metadata_len_byte) * 16
                    if metadata_len > 0:
                        _metadata = response.raw.read(metadata_len)
                        metadata = _metadata.decode('iso-8859-2', errors='ignore')
                                    
            return metadata

        except Exception as e:
            print(e)

        return metadata

    async def _loop(self):
        next_time = time.monotonic() + self.interval

        while not self._stop_event.is_set():
            try:
                new_metadata = self._read_metadata()
                if new_metadata != "":
                    if self._last_metadata != new_metadata:
                        self._last_metadata = new_metadata
                    
                        self.last_title = extract_title(self._last_metadata)

                        if self.coro:
                            await self.coro(self.last_title)

                delay = next_time - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)

            except Exception as e:
                print(e)
            finally:
                next_time +=self.interval

        return True


    async def _main(self):
        self._task = asyncio.create_task(self._loop())
        await self._task
        self._session.close()

    def start(self):
        if isinstance(self._session,requests.Session):
            self._session.close()

        self._session = requests.Session()
        self._session.headers.update({
        'Icy-MetaData': '1',  # Request metadata
        'User-Agent': 'RadioMetadataExtractor/1.0'
        })


        if self._is_running:
            return

        if self._stop_event.is_set():
            self._stop_event.clear()

        asyncio.run(self._main())
        
        self._is_running = True

            
    def stop(self):
        self._stop_event.set()
        

async def print_coro(title)->bool:
    print(title)
    return True




# Usage for Radio Most
if __name__ == "__main__":
    stream_url = "http://stream.radiomost.hu:8200/mobil.aac"
    stream_url = "http://stream.radiomost.hu:8200/live.mp3"
    
    h = NowPlayingExtractHandler(source_url=stream_url,coro=print_coro)
    h.start()