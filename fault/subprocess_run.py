import os
import shlex
from pathlib import Path
from subprocess import Popen, PIPE, CompletedProcess

from fault import FaultConfig

# Terminal formatting codes
MAGENTA = '\x1b[35m'
CYAN = '\x1b[36m'
RED = '\x1b[31m'
BRIGHT = '\x1b[1m'
RESET_ALL = '\x1b[0m'


class PrintDisplay:
    def __init__(self, mode):
        self.mode = mode
        self.lines = []

    def print(self, line):
        line = line.rstrip()
        if self.mode == 'realtime':
            print(line)
        elif self.mode == 'on_error':
            self.lines.append(line)
        else:
            raise Exception('Invalid mode.')

    def error_printing(self):
        if self.mode == 'on_error':
            for line in self.lines:
                print(line)

    def process_output(self, fd, name):
        # generic line-processing function to display lines
        # as they are produced as output in and check for errors.

        retval = ''
        any_line = False
        for line in fd:
            # Add line to value to be returned
            retval += line

            # Display opening text if needed
            if not any_line:
                any_line = True
                self.print(MAGENTA + BRIGHT + f'<{name}>' + RESET_ALL)

            # display if desired
            self.print(line)

        # Display closing text if needed
        if any_line:
            self.print(MAGENTA + BRIGHT + f'</{name}>' + RESET_ALL)

        # Return the full output contents for further processing
        return retval


def make_cmd_str(args):
    return ' '.join(shlex.quote(arg) for arg in args)


def write_script(cmds, env, cwd, script):
    # determine file name
    file_name = (cwd / f'{script}').resolve()
    # write file contents
    with open(file_name, 'w') as f:
        # run in a clean environment
        # ref: https://unix.stackexchange.com/questions/98829
        f.write('''\
#!/bin/sh
[ -z "$CLEANED" ] && exec /bin/env -i CLEANED=1 /bin/sh "$0" "$@"
''')
        # export all variables that should be set for
        # the command(s) to follow
        for key, val in env.items():
            f.write(f'export {key}="{val}"\n')
        # write the commands themselves in order
        for cmd in cmds:
            f.write(make_cmd_str(cmd) + '\n')
    # set file permissions
    file_name.chmod(0o755)


def resolve_env(env=None, use_fault_cfg=True, add_to_env=None):
    # if the user provided an environment, just use that
    if env is not None:
        return env

    # otherwise either use the environment from FaultConfig
    # or a copy of the current environment
    if use_fault_cfg:
        env = FaultConfig.env
    else:
        env = os.environ.copy()

    # add more environment variables if desired
    if add_to_env is not None:
        env.update(**add_to_env)

    # return the environment
    return env


def subprocess_run_batch(cmds, cwd=None, env=None, use_fault_cfg=True,
                         add_to_env=None, script=None, **kwargs):
    # make the working directory if needed
    if cwd is None:
        cwd = FaultConfig.cwd
    cwd = Path(cwd).resolve()
    os.makedirs(cwd, exist_ok=True)

    # determine the environment to use
    env = resolve_env(env=env, use_fault_cfg=use_fault_cfg,
                      add_to_env=add_to_env)

    # write script if desired
    if script is not None:
        write_script(cmds=cmds, env=env, cwd=cwd, script=script)

    # run the commands in sequence and return list of CompletedProcess objects
    return [subprocess_run(cmd, cwd=cwd, env=env, script=None, **kwargs)
            for cmd in cmds]


def subprocess_run(args, cwd=None, env=None, disp_type='on_error', err_str=None,
                   chk_ret_code=True, shell=False, use_fault_cfg=True,
                   add_to_env=None, script=None):
    # "Deluxe" version of subprocess.run that can display STDOUT lines as they
    # come in, looks for errors in STDOUT and STDERR (raising an exception if
    # one is found), and can check the return code from the subprocess
    # (raising an exception if non-zero).
    #
    # The return value is a CompletedProcess, which has properties
    # "returncode", "stdout", and "stderr".  This allows for further processing
    # of the results if desired.
    #
    # Note that only STDOUT is displayed in realtime; STDERR is displayed in
    # its entirety after the subprocess runs.  This is mainly to avoid the
    # confusion that arises when interleaving STDOUT and STDERR.
    #
    # args: List of arguments, with the same meaning as subprocess.run.
    #       Unlike subprocess.run, however, this should always be a list.
    # cwd: Directory in which to run the subprocess.
    # env: Dictionary representing the environment variables to be set
    #      while running the subprocess.  In None, defaults are determined
    #      based on one of the optional fault user config files.
    # disp_type: 'on_error', 'realtime'.  If 'on_error', only print if there
    #            is an error.  If 'realtime', print out STDOUT as lines come
    #            in, then print STDERR after the process completes.
    # err_str: If not None, look for err_str in each line of STDOUT and
    #          STDERR, raising an AssertionError if it is found.
    # chk_ret_code: If True, check the return code after the subprocess runs,
    #               raising an AssertionError if it is non-zero.
    # shell: If True, shell-quote arguments and concatenate using spaces into
    #        a string, then Popen with shell=True.  This is sometimes needed
    #        when running programs if they are actually scripts (e.g.,
    #        Verilator)
    # use_fault_cfg: If True (default) and env is None, then use FaultConfig
    #                to fill in default environment variables.
    # add_to_env: Dictionary of environment variables to add to the
    #             "env" (which is either user-provided or set using
    #             the fault defaults)
    # script: Name of the script to be generated.  if "None", don't generate
    #         a script.  The script generation capability is useful for
    #         debugging commands.

    # setup the environment
    env = resolve_env(env=env, use_fault_cfg=use_fault_cfg,
                      add_to_env=add_to_env)

    # make the directory if needed
    if cwd is None:
        cwd = FaultConfig.cwd
    cwd = Path(cwd).resolve()
    os.makedirs(cwd, exist_ok=True)

    # write script if desired
    if script is not None:
        write_script(cmds=[args], env=env, cwd=cwd, script=script)

    # make sure all arguments have been converted to strings
    args = [f'{arg}' for arg in args]

    # set up printing
    display = PrintDisplay(mode=disp_type)

    # print out the command in a format that can be copy-pasted
    # directly into a terminal (i.e., with proper quoting of arguments)
    cmd_str = make_cmd_str(args)
    display.print(CYAN + BRIGHT + 'Running command: ' + RESET_ALL + cmd_str)

    # combine arguments into a string if needed for shell=True
    if shell:
        args = cmd_str

    # run the subprocess
    err_msg = []
    with Popen(args, cwd=cwd, env=env, stdout=PIPE, stderr=PIPE, bufsize=1,
               universal_newlines=True, shell=shell) as p:

        # print out STDOUT, then STDERR
        # threads could be used here but pytest does not detect exceptions
        # in child threads, so for now the outputs are printed sequentially
        stdout = display.process_output(fd=p.stdout, name='STDOUT')
        stderr = display.process_output(fd=p.stderr, name='STDERR')

        # get return code and check result if desired
        returncode = p.wait()

        if chk_ret_code and returncode:
            err_msg += [f'Got return code {returncode}.']

        # look for errors in STDOUT or STDERR
        if err_str is not None:
            if err_str in stdout:
                err_msg += [f'Found "{err_str}" in STDOUT.']
            if err_str in stderr:
                err_msg += [f'Found "{err_str}" in STDERR.']

    # if any errors were found, print out STDOUT and STDERR if they haven't
    # already been printed, then print out what the error(s) were and
    # raise an exception
    if len(err_msg) != 0:
        display.error_printing()
        print(RED + BRIGHT + f'Found {len(err_msg)} error(s):' + RESET_ALL)
        for k, e in enumerate(err_msg):
            print(RED + BRIGHT + f'{k+1}) {e}' + RESET_ALL)
        raise AssertionError

    # if there were no errors, then return directly
    return CompletedProcess(args=args, returncode=returncode,
                            stdout=stdout, stderr=stderr)
