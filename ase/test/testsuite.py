import os
import sys
import subprocess
from contextlib import contextmanager
import importlib
from multiprocessing import Process, cpu_count, Queue
import tempfile
import unittest
from glob import glob
import runpy
import time
import traceback
import warnings
from pathlib import Path
import argparse

import pytest

from ase.calculators.calculator import names as calc_names, get_calculator_class
from ase.utils import devnull, ExperimentalFeatureWarning
from ase.cli.info import print_info

TEST_BASEPATH = Path(__file__).parent


def importorskip(module):
    # The pytest importorskip() function raises a wierd exception
    # which claims to come from the builtins module, but doesn't!
    #
    # That exception messes with our pipeline when sending stacktraces
    # through multiprocessing.  Argh.
    #
    # We provide our own implementation then!
    try:
        return importlib.import_module(module)
    except ImportError:  # From py3.6 we can use ModuleNotFoundError
        raise unittest.SkipTest('Optional module not present: {}'
                                .format(module))


test_calculator_names = ['emt']


def require(calcname):
    if calcname not in test_calculator_names:
        raise unittest.SkipTest('use --calculators={0} to enable'
                                .format(calcname))


def get_tests(files=None):
    dirname, _ = os.path.split(__file__)
    if files:
        fnames = [os.path.join(dirname, f) for f in files]

        files = set()
        for fname in fnames:
            newfiles = glob(fname)
            if not newfiles:
                raise OSError('No such test: {}'.format(fname))
            files.update(newfiles)
        files = list(files)
    else:
        files = glob(os.path.join(dirname, '*'))
        files.remove(os.path.join(dirname, 'testsuite.py'))

    sdirtests = []  # tests from subdirectories: only one level assumed
    tests = []
    for f in files:
        if os.path.isdir(f):
            # add test subdirectories (like calculators)
            sdirtests.extend(glob(os.path.join(f, '*.py')))
        else:
            # add py files in testdir
            if f.endswith('.py'):
                tests.append(f)
    tests.sort()
    sdirtests.sort()
    tests.extend(sdirtests)  # run test subdirectories at the end
    tests = [os.path.relpath(test, dirname)
             for test in tests if not test.endswith('__.py')]
    return tests


def runtest_almost_no_magic(test):
    dirname, _ = os.path.split(__file__)
    path = os.path.join(dirname, test)
    # exclude some test for windows, not done automatic
    if os.name == 'nt':
        skip = [name for name in calc_names]
        skip += ['db_web', 'h2.py', 'bandgap.py', 'al.py',
                 'runpy.py', 'oi.py']
        if any(s in test for s in skip):
            raise unittest.SkipTest('not on windows')
    try:
        runpy.run_path(path, run_name='test')
    except ImportError as ex:
        module = ex.args[0].split()[-1].replace("'", '').split('.')[0]
        if module in ['matplotlib', 'Scientific', 'lxml', 'Tkinter',
                      'flask', 'gpaw', 'GPAW', 'netCDF4', 'psycopg2', 'kimpy']:
            raise unittest.SkipTest('no {} module'.format(module))
        else:
            raise
    # unittest.main calls sys.exit, which raises SystemExit.
    # Uncatched SystemExit, a subclass of BaseException, marks a test as ERROR
    # even if its exit code is zero (test passes).
    # Here, AssertionError is raised to mark a test as FAILURE if exit code is
    # non-zero.
    except SystemExit as ex:
        if ex.code != 0:
            raise AssertionError


def run_single_test(filename, verbose, strict):
    """Execute single test and return results as dictionary."""
    result = Result(name=filename)

    # Some tests may write to files with the same name as other tests.
    # Hence, create new subdir for each test:
    cwd = os.getcwd()
    testsubdir = filename.replace(os.sep, '_').replace('.', '_')
    result.workdir = os.path.abspath(testsubdir)
    os.mkdir(testsubdir)
    os.chdir(testsubdir)
    t1 = time.time()

    if not verbose:
        sys.stdout = devnull
    try:
        with warnings.catch_warnings():
            if strict:
                # We want all warnings to be errors.  Except some that are
                # normally entirely ignored by Python, and which we don't want
                # to bother about.
                warnings.filterwarnings('error')
                for warntype in [PendingDeprecationWarning, ImportWarning,
                                 ResourceWarning]:
                    warnings.filterwarnings('ignore', category=warntype)

            # This happens from matplotlib sometimes.
            # How can we allow matplotlib to import badly and yet keep
            # a higher standard for modules within our own codebase?
            warnings.filterwarnings('ignore',
                                    'Using or importing the ABCs from',
                                    category=DeprecationWarning)

            # It is okay that we are testing our own experimental features:
            warnings.filterwarnings('ignore',
                                    category=ExperimentalFeatureWarning)
            runtest_almost_no_magic(filename)
    except KeyboardInterrupt:
        raise
    except unittest.SkipTest as ex:
        result.status = 'SKIPPED'
        result.whyskipped = str(ex)
        result.exception = ex
    except AssertionError as ex:
        result.status = 'FAIL'
        result.exception = ex
        result.traceback = traceback.format_exc()
    except BaseException as ex:
        result.status = 'ERROR'
        result.exception = ex
        result.traceback = traceback.format_exc()
    else:
        result.status = 'OK'
    finally:
        sys.stdout = sys.__stdout__
        t2 = time.time()
        os.chdir(cwd)

    result.time = t2 - t1
    return result


class Result:
    """Represents the result of a test; for communicating between processes."""
    attributes = ['name', 'pid', 'exception', 'traceback', 'time', 'status',
                  'whyskipped', 'workdir']

    def __init__(self, **kwargs):
        d = {key: None for key in self.attributes}
        d['pid'] = os.getpid()
        for key in kwargs:
            assert key in d
            d[key] = kwargs[key]
        self.__dict__ = d


def runtests_subprocess(task_queue, result_queue, verbose, strict):
    """Main test loop to be called within subprocess."""

    try:
        while True:
            result = test = None

            test = task_queue.get()
            if test == 'no more tests':
                return

            # We need to run some tests on master:
            #  * doctest exceptions appear to be unpicklable.
            #    Probably they contain a reference to a module or something.
            #  * gui/run may deadlock for unknown reasons in subprocess
            #  * Anything that uses matplotlib (we don't know why)
            #  * pubchem (https://gitlab.com/ase/ase/merge_requests/1477)

            t = test.replace('\\', '/')

            if t in ['bandstructure.py',
                     'bandstructure_many.py',
                     'doctests.py', 'gui/run.py',
                     'matplotlib_plot.py', 'fio/oi.py', 'fio/v_sim.py',
                     'forcecurve.py', 'neb.py',
                     'fio/animate.py', 'db/db_web.py', 'x3d.py',
                     'pubchem.py']:
                result = Result(name=test, status='please run on master')
                result_queue.put(result)
                continue

            result = run_single_test(test, verbose, strict)

            # Any subprocess that uses multithreading is unsafe in
            # subprocesses due to a fork() issue:
            #   https://gitlab.com/ase/ase/issues/244
            # Matplotlib uses multithreading and we must therefore make sure
            # that any test which imports matplotlib runs on master.
            # Hence check whether matplotlib was somehow imported:
            assert 'matplotlib' not in sys.modules, test
            result_queue.put(result)

    except KeyboardInterrupt:
        print('Worker pid={} interrupted by keyboard while {}'
              .format(os.getpid(),
                      'running ' + test if test else 'not running'))
    except BaseException as err:
        # Failure outside actual test -- i.e. internal test suite error.
        result = Result(pid=os.getpid(), name=test, exception=err,
                        traceback=traceback.format_exc(),
                        time=0.0, status='ABORT')
        result_queue.put(result)


def print_test_result(result):
    msg = result.status
    if msg == 'SKIPPED':
        msg = 'SKIPPED: {}'.format(result.whyskipped)
    print('{name:36} {time:6.2f}s {msg}'
          .format(name=result.name, time=result.time, msg=msg))
    if result.traceback:
        print('=' * 78)
        print('Error in {} on pid {}:'.format(result.name, result.pid))
        print('Workdir: {}'.format(result.workdir))
        print(result.traceback.rstrip())
        print('=' * 78)


def runtests_parallel(nprocs, tests, verbose, strict):
    # Test names will be sent, and results received, into synchronized queues:
    task_queue = Queue()
    result_queue = Queue()

    for test in tests:
        task_queue.put(test)

    for i in range(nprocs):  # Each process needs to receive this
        task_queue.put('no more tests')

    procs = []
    try:
        # Start tasks:
        for i in range(nprocs):
            p = Process(target=runtests_subprocess,
                        name='ASE-test-worker-{}'.format(i),
                        args=[task_queue, result_queue, verbose, strict])
            procs.append(p)
            p.start()

        # Collect results:
        for i in range(len(tests)):
            if nprocs == 0:
                # No external workers so we do everything.
                task = task_queue.get()
                result = run_single_test(task, verbose, strict)
            else:
                result = result_queue.get()  # blocking call
                if result.status == 'please run on master':
                    result = run_single_test(result.name, verbose, strict)
            print_test_result(result)
            yield result

            if result.status == 'ABORT':
                raise RuntimeError('ABORT: Internal error in test suite')
    except KeyboardInterrupt:
        raise
    except BaseException:
        for proc in procs:
            proc.terminate()
        raise
    finally:
        for proc in procs:
            proc.join()


def summary(results):
    ntests = len(results)
    err = [r for r in results if r.status == 'ERROR']
    fail = [r for r in results if r.status == 'FAIL']
    skip = [r for r in results if r.status == 'SKIPPED']
    ok = [r for r in results if r.status == 'OK']

    if fail or err:
        print()
        print('Failures and errors:')
        for r in err + fail:
            print('{}: {}: {}'.format(r.name, r.exception.__class__.__name__,
                                      r.exception))

    print('========== Summary ==========')
    print('Number of tests   {:3d}'.format(ntests))
    print('Passes:           {:3d}'.format(len(ok)))
    print('Failures:         {:3d}'.format(len(fail)))
    print('Errors:           {:3d}'.format(len(err)))
    print('Skipped:          {:3d}'.format(len(skip)))
    print('=============================')

    if fail or err:
        print('Test suite failed!')
    else:
        print('Test suite passed!')


def disable_calculators(names):
    for name in names:
        if name in ['emt', 'lj', 'eam', 'morse', 'tip3p']:
            continue
        try:
            cls = get_calculator_class(name)
        except ImportError:
            pass
        else:
            def get_mock_init(name):
                def mock_init(obj, *args, **kwargs):
                    raise unittest.SkipTest('use --calculators={0} to enable'
                                            .format(name))
                return mock_init

            def mock_del(obj):
                pass
            cls.__init__ = get_mock_init(name)
            cls.__del__ = mock_del


def cli(command, calculator_name=None):
    if (calculator_name is not None and
        calculator_name not in test_calculator_names):
        return
    actual_command = ' '.join(command.split('\n')).strip()
    proc = subprocess.Popen(actual_command,
                            shell=True,
                            stdout=subprocess.PIPE)
    print(proc.stdout.read().decode())
    proc.wait()

    if proc.returncode != 0:
        raise RuntimeError('Command "{}" exited with error code {}'
                           .format(actual_command, proc.returncode))


class must_raise:
    """Context manager for checking raising of exceptions."""
    def __init__(self, exception):
        self.exception = exception

    def __enter__(self):
        pass

    def __exit__(self, exc_type, exc_value, tb):
        if exc_type is None:
            raise RuntimeError('Failed to fail: ' + str(self.exception))
        return issubclass(exc_type, self.exception)


@contextmanager
def must_warn(category):
    with warnings.catch_warnings(record=True) as ws:
        yield
        did_warn = any(w.category == category for w in ws)
    if not did_warn:
        raise RuntimeError('Failed to warn: ' + str(category))


@contextmanager
def no_warn():
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore')
        yield


MULTIPROCESSING_MAX_WORKERS = 32
MULTIPROCESSING_DISABLED = 0
MULTIPROCESSING_AUTO = -1


class CLICommand:
    """Run ASE's test-suite.

    By default, tests for external calculators are skipped.  Enable with
    "-c name".
    """

    @staticmethod
    def add_arguments(parser):
        parser.add_argument(
            '-c', '--calculators',
            help='comma-separated list of calculators to test')
        parser.add_argument('--list', action='store_true',
                            help='print all tests and exit')
        parser.add_argument('--list-calculators', action='store_true',
                            help='print all calculator names and exit')
        parser.add_argument('-j', '--jobs', type=int, metavar='N',
                            default=MULTIPROCESSING_AUTO,
                            help='number of worker processes.  '
                            'By default use all available processors '
                            'up to a maximum of {}.  '
                            '0 disables multiprocessing'
                            .format(MULTIPROCESSING_MAX_WORKERS))
        parser.add_argument('-v', '--verbose', action='store_true',
                            help='write test outputs to stdout.  '
                            'Mostly useful when inspecting a single test')
        parser.add_argument('--strict', action='store_true',
                            help='convert warnings to errors')
        parser.add_argument('--nogui', action='store_true',
                            help='do not run graphical tests')
        parser.add_argument('tests', nargs='*',
                            help='specify particular test files.  '
                            'Glob patterns are accepted.')
        parser.add_argument('--pytest', nargs=argparse.REMAINDER,
                            help='forward all remaining arguments to pytest.  '
                            'See pytest --help')

    @staticmethod
    def run(args):
        if args.calculators:
            calculators = args.calculators.split(',')
            os.environ['ASE_TEST_CALCULATORS'] = ' '.join(calculators)
        else:
            calculators = []

        print_info()

        if args.list_calculators:
            for name in calc_names:
                print(name)
            sys.exit(0)

        for calculator in calculators:
            if calculator not in calc_names:
                sys.stderr.write('No calculator named "{}".\n'
                                 'Possible CALCULATORS are: '
                                 '{}.\n'.format(calculator,
                                                ', '.join(calc_names)))
                sys.exit(1)

        if args.nogui:
            os.environ.pop('DISPLAY')

        pytest_args = ['-v']

        def add_args(*args):
            pytest_args.extend(args)

        if args.list:
            add_args('--collect-only')

        if args.jobs == MULTIPROCESSING_DISABLED:
            pass
        elif args.jobs == MULTIPROCESSING_AUTO:
            add_args('--numprocesses=auto',
                     '--maxprocesses={}'.format(MULTIPROCESSING_MAX_WORKERS))
        else:
            add_args('--numprocesses={}'.format(args.jobs))

        add_args('--pyargs')

        if args.tests:
            from ase.test.newtestsuite import TestModule

            dct = TestModule.all_test_modules_as_dict()

            for testname in args.tests:
                mod = dct[testname]
                if mod.is_pytest_style:
                    pytest_args.append(mod.module)
                else:
                    # XXX Not totally logical
                    add_args('ase.test.test_modules::{}'
                             .format(mod.pytest_function_name))
        else:
            add_args('ase.test')

        if args.verbose:
            add_args('--capture=no')

        if args.pytest:
            add_args(*args.pytest)

        print()
        calcstring = ','.join(calculators) if calculators else 'none'
        print('Enabled calculators: {}'.format(calcstring))
        print()
        print('About to run pytest with these parameters:')
        for line in pytest_args:
            print('    ' + line)
        exitcode = pytest.main(pytest_args)
        sys.exit(exitcode)
