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
        if operation == 'lsjson' and ':' not in args[2]:
            return super().run(args, **kwargs)
        if operation == 'version':
            return 'rclone simulated S3 transport via Moto'
        if operation == 'config':
            return json.dumps({'aws': {'type': 's3'}})
        if operation == 'copyto':
            self.last_stderr = '{"msg":"Copied (new)"}'
            source, target = args[2:4]
            if target.startswith('/'):
                bucket, key = self.split(source)
                Path(target).write_bytes(self.client.get_object(Bucket=bucket, Key=key)['Body'].read())
                return ''
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
        if operation == 'copy':
            reports = []
            for name in kwargs['input'].splitlines():
                command = [args[0], 'copyto', str(Path(args[2]) / name), args[3].rstrip('/') + '/' + name,
                           '--s3-storage-class', args[args.index('--s3-storage-class') + 1]]
                self.run(command)
                reports.append(json.dumps({'msg': 'Copied (new)', 'object': name}))
            self.last_stderr = '\n'.join(reports)
            return ''
        if operation == 'delete':
            bucket, prefix = self.split(args[2])
            key = prefix.rstrip('/') + '/' + kwargs['input'].strip()
            self.client.delete_object(Bucket=bucket, Key=key)
            return ''
        if operation == 'lsjson':
            bucket, prefix = self.split(args[2])
            if '--stat' in args:
                obj = self.client.head_object(Bucket=bucket, Key=prefix)
                mtime = obj.get('Metadata', {}).get('mtime')
                if mtime:
                    seconds, _, fraction = mtime.partition('.')
                    modified = datetime.fromtimestamp(int(seconds), timezone.utc).strftime('%Y-%m-%dT%H:%M:%S') + '.' + fraction.ljust(9, '0') + 'Z'
                else:
                    modified = obj['LastModified'].isoformat()
                return json.dumps({'Path': prefix.rsplit('/', 1)[-1], 'Size': obj['ContentLength'],
                                   'ModTime': modified, 'Hashes': {'md5': obj['ETag'].strip('"')}})
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
            if '--exclude-from' in args:
                from sambinha.filtering import Selection
                from sambinha.config import Config
                selected = Selection(Path(args[args.index('--exclude-from') + 1]), Config(jobs=()), self).include_paths(i['Path'] for i in items)
                items = [i for i in items if i['Path'] in selected]
            return json.dumps(items)
        raise AssertionError(f'Unexpected rclone operation: {operation}')
