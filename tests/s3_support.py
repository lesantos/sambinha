"""rclone command transport simulated by Moto's existing S3 API emulator.

The application still uses its real Storage adapter and packing/retention
strategies. Only the external rclone/S3 boundary is replaced; real HTTP tests
exercise that boundary separately on machines allowing loopback networking.
"""
import hashlib
from datetime import datetime, timezone
import json
from pathlib import Path

from sambinha.process import Runner


class MotoRunner(Runner):
    def __init__(self, client):
        super().__init__()
        self.client = client
        self.commands = []

    def split(self, target):
        path = target.split(':', 1)[1]
        bucket, _, key = path.partition('/')
        return bucket, key

    def run(self, args, **kwargs):
        if args[0] != 'rclone':
            return super().run(args, **kwargs)
        self.commands.append(list(args))
        operation = args[1]
        if operation == 'version':
            return 'rclone simulated S3 transport via Moto'
        if operation == 'config':
            return json.dumps({'aws': {'type': 's3'}})
        if operation == 'copyto':
            self.last_stderr = '{"msg":"Copied (new)"}'
            source, target = args[2:4]
            bucket, key = self.split(target)
            storage_class = args[args.index('--s3-storage-class') + 1]
            if ':' in source and not source.startswith('/'):
                src_bucket, src_key = self.split(source)
                self.client.copy_object(Bucket=bucket, Key=key,
                    CopySource={'Bucket': src_bucket, 'Key': src_key}, StorageClass=storage_class)
            else:
                payload = Path(source).read_bytes()
                ns = Path(source).stat().st_mtime_ns
                self.client.put_object(Bucket=bucket, Key=key, Body=payload, StorageClass=storage_class,
                                       Metadata={'mtime': f'{ns // 1000000000}.{ns % 1000000000:09d}'})
            return ''
        if operation == 'delete':
            bucket, prefix = self.split(args[2])
            key = prefix.rstrip('/') + '/' + kwargs['input'].strip()
            self.client.delete_object(Bucket=bucket, Key=key)
            return ''
        if operation == 'lsjson':
            bucket, prefix = self.split(args[2])
            prefix = prefix.rstrip('/') + '/' if prefix else ''
            items = []
            for page in self.client.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix=prefix):
                for item in page.get('Contents', []):
                    metadata = self.client.head_object(Bucket=bucket, Key=item['Key']).get('Metadata', {})
                    mtime = metadata.get('mtime')
                    if mtime:
                        seconds, _, fraction = mtime.partition('.')
                        date = datetime.fromtimestamp(int(seconds), timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
                        modified = date + '.' + fraction.ljust(9, '0') + 'Z'
                    else:
                        modified = item['LastModified'].isoformat()
                    items.append({'Path': item['Key'].removeprefix(prefix), 'Size': item['Size'],
                                  'ModTime': modified,
                                  'Hashes': {'md5': item['ETag'].strip('"')}})
            return json.dumps(items)
        raise AssertionError(f'Unexpected rclone operation: {operation}')
