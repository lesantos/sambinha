import hashlib
import io
import json
import tarfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from sambinha.batches import plan
from sambinha.errors import BackupError, ForeignStateError, UnsupportedStateError
from sambinha.index_generations import listing, remote_index
from sambinha.inventory import File, scan
from sambinha.state import identity
from tests.helpers import Sandbox


class RecoveryGenerationValidationTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.file('file', b'payload')
        self.job = replace(self.config.jobs[0], logic='batch', type='s3', destination='aws:bucket')
        batch = plan(scan(self.source), 100)[0]
        self.control = {**batch.control(), 'archive_size': 12, 'archive_md5': 'a' * 32}
        self.key = batch.key
        self.state_data = {'schema': 1, 'identity': identity(self.job),
                           'active': {self.key:self.control}, 'retired': []}
        self.objects = {}
        self.entries = {self.key:12, self.key+'.json':200}
        self.storage = Mock(job=self.job)
        self.storage.get.side_effect = lambda key,target:Path(target).write_bytes(self.objects[key])

    def add_generation(self, number, *, state=None, marker=None, link=False, control_link=False):
        generation = f'{number:020d}-'+'a'*32
        data = dict(self.state_data if state is None else state)
        data['generation'] = generation
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w:gz') as tar:
            payload = json.dumps(data).encode()
            member = tarfile.TarInfo('state.json');member.size=len(payload)
            if link:member.type=tarfile.SYMTYPE;member.linkname='/etc/passwd';member.size=0
            tar.addfile(member, io.BytesIO(payload))
            payload = json.dumps(self.control).encode()
            member=tarfile.TarInfo('controls/'+self.key+'.json');member.size=len(payload)
            if control_link:member.type=tarfile.SYMTYPE;member.linkname='/etc/passwd';member.size=0
            tar.addfile(member,io.BytesIO(payload))
        payload=stream.getvalue()
        prefix='.sambinha/indexes/'+generation
        self.objects[prefix+'.tar.gz']=payload
        self.objects[prefix+'.commit.json']=json.dumps(marker if marker is not None else {
            'generation':generation,'size':len(payload),'sha256':hashlib.sha256(payload).hexdigest()}).encode()
        self.entries.update({k:len(v) for k,v in self.objects.items()})
        return generation

    def recover(self):
        work=self.root/'work';work.mkdir(exist_ok=True)
        return remote_index(self.config,self.job,self.storage,work,entries=self.entries)

    def test_marker_corruption_falls_back_to_older_confirmed_generation(self):
        old=self.add_generation(1)
        self.add_generation(2,marker=['invalid marker'])
        state,controls=self.recover()
        self.assertEqual(state['generation'],old)
        self.assertEqual(controls[self.key],self.control)

    def test_unconfirmed_new_archive_is_never_selected(self):
        old=self.add_generation(1)
        new=self.add_generation(2)
        del self.entries['.sambinha/indexes/'+new+'.commit.json']
        self.assertEqual(self.recover()[0]['generation'],old)

    def test_bad_checksum_duplicate_path_and_links_fail_closed(self):
        for kind in ('checksum','duplicate','state-link','control-link','missing-control','missing-archive','size'):
            with self.subTest(kind=kind):
                self.objects.clear();self.entries={self.key:12,self.key+'.json':200}
                state=json.loads(json.dumps(self.state_data))
                if kind=='duplicate':state['active'][self.key]['files']*=2
                marker={'generation':'bad','size':0,'sha256':'bad'} if kind=='checksum' else None
                self.add_generation(1,state=state,marker=marker,link=kind=='state-link',control_link=kind=='control-link')
                if kind=='missing-control':del self.entries[self.key+'.json']
                if kind=='missing-archive':del self.entries[self.key]
                if kind=='size':self.entries[self.key]=1
                with self.assertRaises(BackupError):self.recover()

    def test_another_jobs_identity_cannot_be_adopted(self):
        # Name-based identity: another job name or another S3 destination is
        # foreign even when the newest generation carries it.
        for key,value in (('name','other'),('destination','aws:other-bucket')):
            with self.subTest(key=key):
                self.objects.clear();self.entries={self.key:12,self.key+'.json':200}
                self.add_generation(1)
                wrong=json.loads(json.dumps(self.state_data));wrong['identity'][key]=value
                self.add_generation(2,state=wrong)
                with self.assertRaisesRegex(BackupError,'outro job'):self.recover()

    def test_legacy_index_is_supported_without_generation_metadata(self):
        self.add_generation(1)
        archive=next(v for k,v in self.objects.items() if k.endswith('.tar.gz'))
        self.objects={'.sambinha/control.tar.gz':archive}
        self.entries={self.key:12,self.key+'.json':200,'.sambinha/control.tar.gz':len(archive)}
        self.assertEqual(self.recover()[0]['active'],self.state_data['active'])

    def test_future_schema_is_not_silently_downgraded_to_an_older_generation(self):
        self.add_generation(1)
        future = dict(self.state_data, schema=999)
        self.add_generation(2, state=future)
        with self.assertRaisesRegex(BackupError, 'não suportada'):
            self.recover()

    def test_identity_and_schema_errors_are_dispatched_by_type_not_message(self):
        self.add_generation(1)
        for error in (ForeignStateError('mensagem arbitrária'), UnsupportedStateError('outra mensagem')):
            with self.subTest(error=type(error).__name__):
                with patch('sambinha.index_generations.load_state', side_effect=error):
                    with self.assertRaises(type(error)) as caught:
                        self.recover()
                    self.assertIs(caught.exception, error)
        # A plain BackupError mentioning the old substrings is still a recoverable failure.
        with patch('sambinha.index_generations.load_state', side_effect=BackupError('pertence a outra; não suportada')):
            with self.assertRaisesRegex(BackupError, 'Nenhuma geração confirmada'):
                self.recover()

    def test_new_empty_destination_and_invalid_listing_are_distinguished(self):
        work=self.root/'work';work.mkdir()
        state,_=remote_index(self.config,self.job,self.storage,work,allow_new=True,entries={})
        self.assertEqual(state['active'],{})
        with self.assertRaises(BackupError):remote_index(self.config,self.job,self.storage,work,entries={})
        for value in ('null','{}','[{"Path":"bad","Size":-1}]','[{"Path":"../bad","Size":2}]'):
            self.storage.command.return_value=value
            with self.subTest(value=value),self.assertRaises(BackupError):listing(self.storage)


class RecoveryPublicationBoundsTests(Sandbox):
    def test_oversized_state_is_never_confirmed(self):
        from sambinha.index_generations import publish
        job=replace(self.config.jobs[0],logic='batch')
        state={'schema':1,'identity':identity(job),'active':{},'retired':[]}
        ctx=Mock(config=self.config,job=job,now=1000,directory=self.root/'transaction',identity=identity(job),controls_directory=self.root/'controls')
        with patch('sambinha.index_generations.MAX_METADATA',64), self.assertRaisesRegex(BackupError,'metadados'):
            publish(ctx,state)
        ctx.storage.put.assert_not_called()

    def test_oversized_retired_control_is_never_confirmed(self):
        from sambinha.index_generations import publish
        from sambinha.state import atomic_json
        job=replace(self.config.jobs[0],logic='batch')
        control=plan([File('f'+str(i),1,'a'*64,'b'*32,100,0o640) for i in range(20)],100)[0].control()
        key=control['archive']
        controls=self.root/'controls'
        atomic_json(controls/(key+'.json'),control)
        state={'schema':1,'identity':identity(job),'active':{},'retired':[
            {'key':key,'expires':1000,'control':key+'.json','files':[f['path'] for f in control['files']]}]}
        ctx=Mock(config=self.config,job=job,now=1000,directory=self.root/'transaction',identity=identity(job),controls_directory=controls)
        with patch('sambinha.index_generations.MAX_METADATA',1800), self.assertRaisesRegex(BackupError,'metadados'):
            publish(ctx,state)
        ctx.storage.put.assert_not_called()

    def test_aggregate_control_limit_blocks_confirmation(self):
        from sambinha.index_generations import publish
        job=replace(self.config.jobs[0],logic='batch')
        control=plan([File('a',1,'a'*64,'b'*32,100,0o640)],100)[0].control()
        state={'schema':1,'identity':identity(job),'active':{control['archive']:control},'retired':[]}
        ctx=Mock(config=self.config,job=job,now=1000,directory=self.root/'transaction',identity=identity(job),controls_directory=self.root/'controls')
        with patch('sambinha.index_generations.MAX_CONTROLS_METADATA',100), self.assertRaisesRegex(BackupError,'metadados'):
            publish(ctx,state)
        ctx.storage.put.assert_not_called()

    def test_limits_fit_a_large_backup_and_errors_report_sizes(self):
        from sambinha import index_generations
        from sambinha.index_generations import publish
        from sambinha.logging import SUMMARY_EVENTS
        self.assertGreaterEqual(index_generations.MAX_METADATA, 256 * 1024 * 1024)
        self.assertGreaterEqual(index_generations.MAX_CONTROLS_METADATA, 512 * 1024 * 1024)
        self.assertIn('index_size', SUMMARY_EVENTS)
        job=replace(self.config.jobs[0],logic='batch')
        state={'schema':1,'identity':identity(job),'active':{},'retired':[]}
        ctx=Mock(config=self.config,job=job,now=1000,directory=self.root/'transaction',identity=identity(job),controls_directory=self.root/'controls')
        with patch('sambinha.index_generations.MAX_METADATA',64), self.assertRaisesRegex(BackupError,r'state\.json\): \d+ bytes, limite 64'):
            publish(ctx,state)

    def test_index_size_event_is_logged_before_upload(self):
        from sambinha.index_generations import publish
        job=replace(self.config.jobs[0],logic='batch')
        control=plan([File('a',1,'a'*64,'b'*32,100,0o640)],100)[0].control()
        state={'schema':1,'identity':identity(job),'active':{control['archive']:control},'retired':[]}
        ctx=Mock(config=self.config,job=job,now=1000,directory=self.root/'transaction',identity=identity(job),controls_directory=self.root/'controls')
        ctx.storage.put.side_effect=RuntimeError('stop before upload')
        with self.assertRaisesRegex(RuntimeError,'stop before upload'):
            publish(ctx,state)
        names=[call.args[0] for call in ctx.event.call_args_list]
        self.assertIn('index_size',names)
        values=next(call.kwargs for call in ctx.event.call_args_list if call.args[0]=='index_size')
        self.assertGreater(values['state_bytes'],0)
        self.assertGreater(values['controls_bytes'],0)
        self.assertEqual(values['controls'],1)
