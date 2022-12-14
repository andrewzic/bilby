import datetime

import numpy as np

from ..utils import logger
from .base_sampler import Sampler, signal_wrapper
from .dynesty import Dynesty, _log_likelihood_wrapper, _prior_transform_wrapper


class DynamicDynesty(Dynesty):
    """
    bilby wrapper of `dynesty.DynamicNestedSampler`
    (https://dynesty.readthedocs.io/en/latest/)

    All positional and keyword arguments (i.e., the args and kwargs) passed to
    `run_sampler` will be propagated to `dynesty.DynamicNestedSampler`, see
    documentation for that class for further help. Under Other Parameter below,
    we list commonly all kwargs and the bilby defaults.

    Parameters
    ==========
    likelihood: likelihood.Likelihood
        A  object with a log_l method
    priors: bilby.core.prior.PriorDict, dict
        Priors to be used in the search.
        This has attributes for each parameter to be sampled.
    outdir: str, optional
        Name of the output directory
    label: str, optional
        Naming scheme of the output files
    use_ratio: bool, optional
        Switch to set whether or not you want to use the log-likelihood ratio
        or just the log-likelihood
    plot: bool, optional
        Switch to set whether or not you want to create traceplots
    skip_import_verification: bool
        Skips the check if the sampler is installed if true. This is
        only advisable for testing environments

    Other Parameters
    ------==========
    bound: {'none', 'single', 'multi', 'balls', 'cubes'}, ('multi')
        Method used to select new points
    sample: {'unif', 'rwalk', 'slice', 'rslice', 'hslice'}, ('rwalk')
        Method used to sample uniformly within the likelihood constraints,
        conditioned on the provided bounds
    walks: int
        Number of walks taken if using `sample='rwalk'`, defaults to `ndim * 5`
    verbose: Bool
        If true, print information information about the convergence during
    check_point: bool,
        If true, use check pointing.
    check_point_delta_t: float (600)
        The approximate checkpoint period (in seconds). Should the run be
        interrupted, it can be resumed from the last checkpoint. Set to
        `None` to turn-off check pointing
    n_check_point: int, optional (None)
        The number of steps to take before check pointing (override
        check_point_delta_t).
    resume: bool
        If true, resume run from checkpoint (if available)
    """

    default_kwargs = dict(
        bound="multi",
        sample="rwalk",
        verbose=True,
        check_point_delta_t=600,
        first_update=None,
        npdim=None,
        rstate=None,
        queue_size=None,
        pool=None,
        use_pool=None,
        logl_args=None,
        logl_kwargs=None,
        ptform_args=None,
        ptform_kwargs=None,
        enlarge=None,
        bootstrap=None,
        vol_dec=0.5,
        vol_check=2.0,
        facc=0.5,
        slices=5,
        walks=None,
        update_interval=0.6,
        nlive_init=500,
        maxiter_init=None,
        maxcall_init=None,
        dlogz_init=0.01,
        logl_max_init=np.inf,
        nlive_batch=500,
        wt_function=None,
        wt_kwargs=None,
        maxiter_batch=None,
        maxcall_batch=None,
        maxiter=None,
        maxcall=None,
        maxbatch=None,
        stop_function=None,
        stop_kwargs=None,
        use_stop=True,
        save_bounds=True,
        print_progress=True,
        print_func=None,
        live_points=None,
    )

    def __init__(
        self,
        likelihood,
        priors,
        outdir="outdir",
        label="label",
        use_ratio=False,
        plot=False,
        skip_import_verification=False,
        check_point=True,
        n_check_point=None,
        check_point_delta_t=600,
        resume=True,
        **kwargs,
    ):
        super(DynamicDynesty, self).__init__(
            likelihood=likelihood,
            priors=priors,
            outdir=outdir,
            label=label,
            use_ratio=use_ratio,
            plot=plot,
            skip_import_verification=skip_import_verification,
            **kwargs,
        )
        self.n_check_point = n_check_point
        self.check_point = check_point
        self.resume = resume
        if self.n_check_point is None:
            # If the log_likelihood_eval_time is not calculable then
            # check_point is set to False.
            if np.isnan(self._log_likelihood_eval_time):
                self.check_point = False
            n_check_point_raw = check_point_delta_t / self._log_likelihood_eval_time
            n_check_point_rnd = int(float(f"{n_check_point_raw:1.0g}"))
            self.n_check_point = n_check_point_rnd

        self.resume_file = f"{self.outdir}/{self.label}_resume.pickle"

    @property
    def external_sampler_name(self):
        return "dynesty"

    @property
    def sampler_function_kwargs(self):
        keys = [
            "nlive_init",
            "maxiter_init",
            "maxcall_init",
            "dlogz_init",
            "logl_max_init",
            "nlive_batch",
            "wt_function",
            "wt_kwargs",
            "maxiter_batch",
            "maxcall_batch",
            "maxiter",
            "maxcall",
            "maxbatch",
            "stop_function",
            "stop_kwargs",
            "use_stop",
            "save_bounds",
            "print_progress",
            "print_func",
            "live_points",
        ]
        return {key: self.kwargs[key] for key in keys}

    @signal_wrapper
    def run_sampler(self):
        import dynesty

        self._setup_pool()
        self.sampler = dynesty.DynamicNestedSampler(
            loglikelihood=_log_likelihood_wrapper,
            prior_transform=_prior_transform_wrapper,
            ndim=self.ndim,
            **self.sampler_init_kwargs,
        )

        if self.check_point:
            out = self._run_external_sampler_with_checkpointing()
        else:
            out = self._run_external_sampler_without_checkpointing()
        self._close_pool()

        # Flushes the output to force a line break
        if self.kwargs["verbose"]:
            print("")

        # self.result.sampler_output = out
        self._generate_result(out)
        if self.plot:
            self.generate_trace_plots(out)

        return self.result

    def _run_external_sampler_with_checkpointing(self):
        logger.debug("Running sampler with checkpointing")
        if self.resume:
            resume = self.read_saved_state(continuing=True)
            if resume:
                logger.info("Resuming from previous run.")

        old_ncall = self.sampler.ncall
        sampler_kwargs = self.sampler_function_kwargs.copy()
        sampler_kwargs["maxcall"] = self.n_check_point
        self.start_time = datetime.datetime.now()
        while True:
            sampler_kwargs["maxcall"] += self.n_check_point
            self.sampler.run_nested(**sampler_kwargs)
            if self.sampler.ncall == old_ncall:
                break
            old_ncall = self.sampler.ncall

            self.write_current_state()

        self._remove_checkpoint()
        return self.sampler.results

    def write_current_state_and_exit(self, signum=None, frame=None):
        Sampler.write_current_state_and_exit(self=self, signum=signum, frame=frame)

    def _verify_kwargs_against_default_kwargs(self):
        Sampler._verify_kwargs_against_default_kwargs(self)
