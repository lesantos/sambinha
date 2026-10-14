"""rclone command transport simulated by Moto's existing S3 API emulator.

The application still uses its real Storage adapter and packing/retention
strategies. Only the external rclone/S3 boundary is replaced; real HTTP tests
exercise that boundary separately on machines allowing loopback networking.
"""
import hashlib
from datetime import datetime, timezone
import fnmatch
import json
from pathlib import Path
from botocore.exceptions import ClientError

from sambinha.process import Runner
from sambinha.errors import BackupError


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
                target_path = Path(target)
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.write_bytes(self.client.get_object(Bucket=bucket, Key=key)['Body'].read())
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
            if not args[2].startswith('/'):
                bucket, prefix = self.split(args[2])
                prefix = prefix.rstrip('/') + '/'
                target_root = Path(args[3])
                for page in self.client.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix=prefix):
                    for item in page.get('Contents', []):
                        name = item['Key'][len(prefix):]
                        target = target_root / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(self.client.get_object(Bucket=bucket, Key=item['Key'])['Body'].read())
                self.last_stderr = ''
                return ''
            reports = []
            for name in kwargs['input'].splitlines():
                command = [args[0], 'copyto', str(Path(args[2]) / name), args[3].rstrip('/') + '/' + name,
                           '--s3-storage-class', args[args.index('--s3-storage-class') + 1]]
                self.run(command)
                reports.append(json.dumps({'msg': 'Copied (new)', 'object': name}))
            self.last_stderr = '\n'.join(reports)
            return ''
        if operation == 'sync':
            source = Path(args[2])
            bucket, prefix = self.split(args[3])
            prefix = prefix.rstrip('/')
            backup_target = args[args.index('--backup-dir') + 1]
            backup_bucket, backup_prefix = self.split(backup_target)
            backup_prefix = backup_prefix.rstrip('/')
            max_delete = int(args[args.index('--max-delete') + 1])
            dry_run = '--dry-run' in args
            candidates = {path.relative_to(source).as_posix(): path for path in source.rglob('*')
                          if path.is_file() and not path.is_symlink()}
            candidates = {name: path for name, path in candidates.items()
                          if not name.startswith('.sambinha/')}
            selection = None
            if '--exclude-from' in args:
                from sambinha.config import Config
                from sambinha.filtering import Selection
                selection = Selection(Path(args[args.index('--exclude-from') + 1]), Config(jobs=()), self)
                selected = selection.include_paths(candidates)
                candidates = {name: path for name, path in candidates.items() if name in selected}
            for flag_index, flag in enumerate(args[:-1]):
                if flag == '--exclude':
                    pattern = args[flag_index + 1]
                    candidates = {name: path for name, path in candidates.items()
                                  if not fnmatch.fnmatchcase(name, pattern.lstrip('/'))}

            remote_prefix = prefix + '/' if prefix else ''
            existing = {}
            for page in self.client.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix=remote_prefix):
                for obj in page.get('Contents', []):
                    name = obj['Key'][len(remote_prefix):]
                    if name and not name.startswith('.sambinha/'):
                        existing[name] = obj
            protected_existing = selection.include_paths(existing) if selection else set(existing)
            removed = protected_existing - set(candidates)
            if max_delete >= 0 and len(removed) > max_delete:
                raise BackupError(f'rclone sync exceeded --max-delete={max_delete}')
            reports = []
            if not dry_run:
                storage_class = args[args.index('--s3-storage-class') + 1]
                def preserve(name):
                    key = remote_prefix + name
                    self.client.copy_object(Bucket=backup_bucket, Key=backup_prefix + '/' + name,
                        CopySource={'Bucket': bucket, 'Key': key}, StorageClass=storage_class,
                        MetadataDirective='COPY')
                for name in sorted(removed):
                    preserve(name)
                    self.client.delete_object(Bucket=bucket, Key=remote_prefix + name)
                for name, path in sorted(candidates.items()):
                    payload = path.read_bytes()
                    stat = path.stat()
                    modified = f'{stat.st_mtime_ns // 1000000000}.{stat.st_mtime_ns % 1000000000:09d}'
                    old = existing.get(name)
                    if old:
                        metadata = self.client.head_object(Bucket=bucket, Key=remote_prefix + name)
                        old_mtime = metadata.get('Metadata', {}).get('mtime')
                        if '--checksum' in args:
                            same = (old['Size'] == len(payload) and
                                    old['ETag'].strip('"') == hashlib.md5(payload, usedforsecurity=False).hexdigest())
                        else:
                            same = old['Size'] == len(payload) and old_mtime == modified
                        if same:
                            continue
                        if '--update' in args and old_mtime and old_mtime > modified:
                            if args[args.index('--log-level') + 1] == 'DEBUG':
                                record = {'level': 'debug', 'msg': 'Skipped copy as --update is in use',
                                          'object': name}
                                reports.append(json.dumps(record))
                                if kwargs.get('on_output'):
                                    kwargs['on_output'](record)
                            continue
                        preserve(name)
                    self.client.put_object(Bucket=bucket, Key=remote_prefix + name, Body=payload,
                        StorageClass=storage_class, Metadata={'mtime': modified})
            self.last_stderr = '\n'.join(reports)
            return ''
        if operation == 'check':
            source = Path(args[2])
            bucket, prefix = self.split(args[3])
            prefix = prefix.rstrip('/')
            remote_prefix = prefix + '/' if prefix else ''
            source_files = {path.relative_to(source).as_posix(): path for path in source.rglob('*')
                            if path.is_file() and not path.is_symlink()
                            and not path.relative_to(source).as_posix().startswith('.sambinha/')}
            if '--exclude-from' in args:
                from sambinha.config import Config
                from sambinha.filtering import Selection
                selection = Selection(Path(args[args.index('--exclude-from') + 1]), Config(jobs=()), self)
                selected = selection.include_paths(source_files)
                source_files = {name: path for name, path in source_files.items() if name in selected}
            for name, path in source_files.items():
                remote = self.client.head_object(Bucket=bucket, Key=remote_prefix + name)
                if remote['ContentLength'] != path.stat().st_size:
                    raise BackupError(f'check failed: size differs for {name}')
            self.last_stderr = ''
            return ''
        if operation == 'delete':
            bucket, prefix = self.split(args[2])
            key = prefix.rstrip('/') + '/' + kwargs['input'].strip()
            self.client.delete_object(Bucket=bucket, Key=key)
            return ''
        if operation == 'purge':
            bucket, prefix = self.split(args[2])
            prefix = prefix.rstrip('/') + '/'
            for page in self.client.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix=prefix):
                for item in page.get('Contents', []):
                    self.client.delete_object(Bucket=bucket, Key=item['Key'])
            return ''
        if operation == 'lsjson':
            bucket, prefix = self.split(args[2])
            if '--stat' in args:
                try:
                    obj = self.client.head_object(Bucket=bucket, Key=prefix)
                except ClientError as error:
                    if error.response.get('Error', {}).get('Code') not in {'404', 'NoSuchKey', 'NotFound'}:
                        raise
                    listing = self.client.list_objects_v2(Bucket=bucket, Prefix=prefix.rstrip('/') + '/',
                                                          MaxKeys=1)
                    if listing.get('KeyCount'):
                        return json.dumps({'Path': prefix.rsplit('/', 1)[-1], 'Size': -1,
                                           'IsDir': True})
                    raise
                mtime = obj.get('Metadata', {}).get('mtime')
                if mtime:
                    seconds, _, fraction = mtime.partition('.')
                    modified = datetime.fromtimestamp(int(seconds), timezone.utc).strftime('%Y-%m-%dT%H:%M:%S') + '.' + fraction.ljust(9, '0') + 'Z'
                else:
                    modified = obj['LastModified'].isoformat()
                return json.dumps({'Path': prefix.rsplit('/', 1)[-1], 'Size': obj['ContentLength'],
                                   'ModTime': modified, 'IsDir': False,
                                   'Hashes': {'md5': obj['ETag'].strip('"')}})
            prefix = prefix.rstrip('/') + '/' if prefix else ''
            items = []
            objects = []
            for page in self.client.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix=prefix):
                objects.extend(page.get('Contents', []))
            if '--dirs-only' in args:
                directories = sorted({key[len(prefix):].split('/', 1)[0]
                                      for item in objects
                                      for key in (item['Key'],)
                                      if '/' in key[len(prefix):]})
                return json.dumps([{'Path': name, 'Name': name, 'IsDir': True} for name in directories])
            for item in objects:
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
