"""Test AdaNet ensemble single graph implementation.

Copyright 2018 The AdaNet Authors. All Rights Reserved.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    https://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import contextlib
import os
import shutil

from absl.testing import parameterized
from adanet.core.ensemble import Candidate as EnsembleCandidate
from adanet.core.ensemble import ComplexityRegularizedEnsembler
from adanet.core.ensemble import MixtureWeightType
from adanet.core.ensemble_builder import _EnsembleBuilder
from adanet.core.ensemble_builder import _SubnetworkManager
from adanet.core.subnetwork import Builder
from adanet.core.subnetwork import Subnetwork
from adanet.core.summary import Summary
import adanet.core.testing_utils as tu
import tensorflow as tf


class _Builder(Builder):

  def __init__(self,
               subnetwork_train_op_fn,
               mixture_weights_train_op_fn,
               use_logits_last_layer,
               seed=42,
               multi_head=False):
    self._subnetwork_train_op_fn = subnetwork_train_op_fn
    self._mixture_weights_train_op_fn = mixture_weights_train_op_fn
    self._use_logits_last_layer = use_logits_last_layer
    self._seed = seed
    self._multi_head = multi_head

  @property
  def name(self):
    return "test"

  def build_subnetwork(self,
                       features,
                       logits_dimension,
                       training,
                       iteration_step,
                       summary,
                       previous_ensemble=None):
    assert features is not None
    assert training is not None
    assert iteration_step is not None
    assert summary is not None

    # Trainable variables collection should always be empty when
    # build_subnetwork is called.
    assert not tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES)

    # Subnetworks get iteration steps instead of global steps.
    global_step = tf.train.get_global_step()
    assert "subnetwork_test/iteration_step" == global_step.op.name

    # Subnetworks get scoped summaries.
    assert "fake_scalar" == tf.summary.scalar("scalar", 1.)
    assert "fake_image" == tf.summary.image("image", 1.)
    assert "fake_histogram" == tf.summary.histogram("histogram", 1.)
    assert "fake_audio" == tf.summary.audio("audio", 1., 1.)
    last_layer = tu.dummy_tensor(shape=(2, 3))

    def logits_fn(logits_dim):
      return tf.layers.dense(
          last_layer,
          units=logits_dim,
          kernel_initializer=tf.glorot_uniform_initializer(seed=self._seed))

    if self._multi_head:
      logits = {
          "head1": logits_fn(logits_dimension / 2),
          "head2": logits_fn(logits_dimension / 2)
      }
      last_layer = {"head1": last_layer, "head2": last_layer}
    else:
      logits = logits_fn(logits_dimension)

    return Subnetwork(
        last_layer=logits if self._use_logits_last_layer else last_layer,
        logits=logits,
        complexity=2,
        persisted_tensors={})

  def build_subnetwork_train_op(self, subnetwork, loss, var_list, labels,
                                iteration_step, summary, previous_ensemble):
    assert iteration_step is not None
    assert summary is not None
    return self._subnetwork_train_op_fn(loss, var_list)

  def build_mixture_weights_train_op(self, loss, var_list, logits, labels,
                                     iteration_step, summary):
    assert iteration_step is not None
    assert summary is not None
    return self._mixture_weights_train_op_fn(loss, var_list)


class _BuilderPrunerAll(_Builder):
  """Removed previous ensemble completely."""

  def prune_previous_ensemble(self, previous_ensemble):
    return []


class _BuilderPrunerLeaveOne(_Builder):
  """Removed previous ensemble completely."""

  def prune_previous_ensemble(self, previous_ensemble):
    if previous_ensemble:
      return [0]
    return []


class _FakeSummary(Summary):
  """A fake adanet.Summary."""

  def scalar(self, name, tensor, family=None):
    return "fake_scalar"

  def image(self, name, tensor, max_outputs=3, family=None):
    return "fake_image"

  def histogram(self, name, values, family=None):
    return "fake_histogram"

  def audio(self, name, tensor, sample_rate, max_outputs=3, family=None):
    return "fake_audio"

  @contextlib.contextmanager
  def current_scope(self):
    yield


class EnsembleBuilderTest(parameterized.TestCase, tf.test.TestCase):

  def setUp(self):
    super(EnsembleBuilderTest, self).setUp()
    # Setup and cleanup test directory.
    self.test_subdirectory = os.path.join(tf.flags.FLAGS.test_tmpdir, self.id())
    shutil.rmtree(self.test_subdirectory, ignore_errors=True)
    os.mkdir(self.test_subdirectory)

  def tearDown(self):
    super(EnsembleBuilderTest, self).tearDown()
    tf.reset_default_graph()
    shutil.rmtree(self.test_subdirectory, ignore_errors=True)

  @parameterized.named_parameters({
      "testcase_name": "no_previous_ensemble",
      "want_logits": [[.016], [.117]],
      "want_loss": 1.338,
      "want_adanet_loss": 1.338,
      "want_mixture_weight_vars": 1,
  }, {
      "testcase_name": "no_previous_ensemble_prune_all",
      "want_logits": [[.016], [.117]],
      "want_loss": 1.338,
      "want_adanet_loss": 1.338,
      "want_mixture_weight_vars": 1,
      "subnetwork_builder_class": _BuilderPrunerAll
  }, {
      "testcase_name": "no_previous_ensemble_prune_leave_one",
      "want_logits": [[.016], [.117]],
      "want_loss": 1.338,
      "want_adanet_loss": 1.338,
      "want_mixture_weight_vars": 1,
      "subnetwork_builder_class": _BuilderPrunerLeaveOne
  }, {
      "testcase_name": "default_mixture_weight_initializer_scalar",
      "mixture_weight_initializer": None,
      "mixture_weight_type": MixtureWeightType.SCALAR,
      "use_logits_last_layer": True,
      "want_logits": [[.580], [.914]],
      "want_loss": 1.362,
      "want_adanet_loss": 1.362,
      "want_mixture_weight_vars": 1,
  }, {
      "testcase_name": "default_mixture_weight_initializer_vector",
      "mixture_weight_initializer": None,
      "mixture_weight_type": MixtureWeightType.VECTOR,
      "use_logits_last_layer": True,
      "want_logits": [[.580], [.914]],
      "want_loss": 1.362,
      "want_adanet_loss": 1.362,
      "want_mixture_weight_vars": 1,
  }, {
      "testcase_name": "default_mixture_weight_initializer_matrix",
      "mixture_weight_initializer": None,
      "mixture_weight_type": MixtureWeightType.MATRIX,
      "want_logits": [[.016], [.117]],
      "want_loss": 1.338,
      "want_adanet_loss": 1.338,
      "want_mixture_weight_vars": 1,
  }, {
      "testcase_name": "default_mixture_weight_initializer_matrix_on_logits",
      "mixture_weight_initializer": None,
      "mixture_weight_type": MixtureWeightType.MATRIX,
      "use_logits_last_layer": True,
      "want_logits": [[.030], [.047]],
      "want_loss": 1.378,
      "want_adanet_loss": 1.378,
      "want_mixture_weight_vars": 1,
  }, {
      "testcase_name": "no_previous_ensemble_use_bias",
      "use_bias": True,
      "want_logits": [[0.013], [0.113]],
      "want_loss": 1.338,
      "want_adanet_loss": 1.338,
      "want_mixture_weight_vars": 2,
  }, {
      "testcase_name": "no_previous_ensemble_predict_mode",
      "mode": tf.estimator.ModeKeys.PREDICT,
      "want_logits": [[0.], [0.]],
  }, {
      "testcase_name": "no_previous_ensemble_lambda",
      "adanet_lambda": .01,
      "want_logits": [[.014], [.110]],
      "want_loss": 1.340,
      "want_adanet_loss": 1.343,
      "want_mixture_weight_vars": 1,
  }, {
      "testcase_name": "no_previous_ensemble_beta",
      "adanet_beta": .1,
      "want_logits": [[.006], [.082]],
      "want_loss": 1.349,
      "want_adanet_loss": 1.360,
      "want_mixture_weight_vars": 1,
  }, {
      "testcase_name": "no_previous_ensemble_lambda_and_beta",
      "adanet_lambda": .01,
      "adanet_beta": .1,
      "want_logits": [[.004], [.076]],
      "want_loss": 1.351,
      "want_adanet_loss": 1.364,
      "want_mixture_weight_vars": 1,
  }, {
      "testcase_name": "multi_head",
      "want_logits": {
          "head1": [[.016], [.117]],
          "head2": [[.016], [.117]],
      },
      "want_loss": 2.675,
      "want_adanet_loss": 2.675,
      "multi_head": True,
      "want_mixture_weight_vars": 2,
      "want_num_trainable_vars": 4,
  })
  def test_build_ensemble_spec(
      self,
      want_logits,
      want_loss=None,
      want_adanet_loss=None,
      want_mixture_weight_vars=None,
      adanet_lambda=0.,
      adanet_beta=0.,
      ensemble_spec_fn=lambda: None,
      use_bias=False,
      use_logits_last_layer=False,
      mixture_weight_type=MixtureWeightType.MATRIX,
      mixture_weight_initializer=tf.zeros_initializer(),
      warm_start_mixture_weights=True,
      subnetwork_builder_class=_Builder,
      mode=tf.estimator.ModeKeys.TRAIN,
      multi_head=False,
      want_num_trainable_vars=2):
    seed = 64
    if multi_head:
      head = tf.contrib.estimator.multi_head(heads=[
          tf.contrib.estimator.binary_classification_head(
              name="head1", loss_reduction=tf.losses.Reduction.SUM),
          tf.contrib.estimator.binary_classification_head(
              name="head2", loss_reduction=tf.losses.Reduction.SUM)
      ])
    else:
      head = tf.contrib.estimator.binary_classification_head(
          loss_reduction=tf.losses.Reduction.SUM)
    builder = _EnsembleBuilder(head=head)

    features = {"x": tf.constant([[1.], [2.]])}
    if multi_head:
      labels = {"head1": tf.constant([0, 1]), "head2": tf.constant([0, 1])}
    else:
      labels = tf.constant([0, 1])

    def _subnetwork_train_op_fn(loss, var_list):
      self.assertLen(var_list, want_num_trainable_vars)
      self.assertEqual(var_list,
                       tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES))
      # Subnetworks get iteration steps instead of global steps.
      self.assertEqual("subnetwork_test/iteration_step",
                       tf.train.get_global_step().op.name)

      # Subnetworks get scoped summaries.
      self.assertEqual("fake_scalar", tf.summary.scalar("scalar", 1.))
      self.assertEqual("fake_image", tf.summary.image("image", 1.))
      self.assertEqual("fake_histogram", tf.summary.histogram("histogram", 1.))
      self.assertEqual("fake_audio", tf.summary.audio("audio", 1., 1.))
      optimizer = tf.train.GradientDescentOptimizer(learning_rate=.1)
      return optimizer.minimize(loss, var_list=var_list)

    def _mixture_weights_train_op_fn(loss, var_list):
      self.assertLen(var_list, want_mixture_weight_vars)
      self.assertEqual(var_list,
                       tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES))
      # Subnetworks get iteration steps instead of global steps.
      self.assertEqual("ensemble_test/iteration_step",
                       tf.train.get_global_step().op.name)

      # Subnetworks get scoped summaries.
      self.assertEqual("fake_scalar", tf.summary.scalar("scalar", 1.))
      self.assertEqual("fake_image", tf.summary.image("image", 1.))
      self.assertEqual("fake_histogram", tf.summary.histogram("histogram", 1.))
      self.assertEqual("fake_audio", tf.summary.audio("audio", 1., 1.))
      optimizer = tf.train.GradientDescentOptimizer(learning_rate=.1)
      return optimizer.minimize(loss, var_list=var_list)

    previous_ensemble = None
    previous_ensemble_spec = ensemble_spec_fn()
    if previous_ensemble_spec:
      previous_ensemble = previous_ensemble_spec.ensemble

    subnetwork_manager = _SubnetworkManager(head)
    subnetwork_builder = subnetwork_builder_class(
        _subnetwork_train_op_fn,
        _mixture_weights_train_op_fn,
        use_logits_last_layer,
        seed,
        multi_head=multi_head)

    subnetwork_spec = subnetwork_manager.build_subnetwork_spec(
        name="test",
        subnetwork_builder=subnetwork_builder,
        iteration_step=tf.train.get_or_create_global_step(),
        summary=_FakeSummary(),
        features=features,
        mode=mode,
        labels=labels,
        previous_ensemble=previous_ensemble)
    ensemble_spec = builder.build_ensemble_spec(
        # Note: when ensemble_spec is not None and warm_start_mixture_weights
        # is True, we need to make sure that the bias and mixture weights are
        # already saved to the checkpoint_dir.
        name="test",
        previous_ensemble_spec=previous_ensemble_spec,
        candidate=EnsembleCandidate("foo", [subnetwork_builder], None),
        ensembler=ComplexityRegularizedEnsembler(
            mixture_weight_type=mixture_weight_type,
            mixture_weight_initializer=mixture_weight_initializer,
            warm_start_mixture_weights=warm_start_mixture_weights,
            model_dir=self.test_subdirectory,
            adanet_lambda=adanet_lambda,
            adanet_beta=adanet_beta,
            use_bias=use_bias),
        subnetwork_specs=[subnetwork_spec],
        summary=_FakeSummary(),
        features=features,
        iteration_number=1,
        iteration_step=tf.train.get_or_create_global_step(),
        labels=labels,
        mode=mode)

    with self.test_session() as sess:
      sess.run(tf.global_variables_initializer())

      # Get the real global step outside a subnetwork's context.
      self.assertEqual("global_step", tf.train.get_global_step().op.name)

      # Get global tf.summary outside a subnetwork's context.
      self.assertNotEqual("fake_scalar", tf.summary.scalar("scalar", 1.))
      self.assertNotEqual("fake_image", tf.summary.image("image", 1.))
      self.assertNotEqual("fake_histogram", tf.summary.histogram(
          "histogram", 1.))
      self.assertNotEqual("fake_audio", tf.summary.audio("audio", 1., 1.))

      if mode == tf.estimator.ModeKeys.PREDICT:
        self.assertAllClose(
            want_logits, sess.run(ensemble_spec.ensemble.logits), atol=1e-3)
        self.assertIsNone(ensemble_spec.loss)
        self.assertIsNone(ensemble_spec.adanet_loss)
        self.assertIsNone(ensemble_spec.train_op)
        self.assertIsNotNone(ensemble_spec.export_outputs)
        return

      # Verify that train_op works, previous loss should be greater than loss
      # after a train op.
      loss = sess.run(ensemble_spec.loss)
      train_op = tf.group(subnetwork_spec.train_op.train_op,
                          ensemble_spec.train_op.train_op)
      for _ in range(3):
        sess.run(train_op)
      self.assertGreater(loss, sess.run(ensemble_spec.loss))

      self.assertAllClose(
          want_logits, sess.run(ensemble_spec.ensemble.logits), atol=1e-3)

      # Bias should learn a non-zero value when used.
      bias = sess.run(ensemble_spec.ensemble.bias)
      if isinstance(bias, dict):
        bias = sum(abs(b) for b in bias.values())
      if use_bias:
        self.assertNotEqual(0., bias)
      else:
        self.assertAlmostEqual(0., bias)

      self.assertAlmostEqual(want_loss, sess.run(ensemble_spec.loss), places=3)
      self.assertAlmostEqual(
          want_adanet_loss, sess.run(ensemble_spec.adanet_loss), places=3)


def _make_metrics(sess,
                  metric_fn,
                  mode=tf.estimator.ModeKeys.EVAL,
                  multi_head=False):

  if multi_head:
    head = tf.contrib.estimator.multi_head(heads=[
        tf.contrib.estimator.binary_classification_head(
            name="head1", loss_reduction=tf.losses.Reduction.SUM),
        tf.contrib.estimator.binary_classification_head(
            name="head2", loss_reduction=tf.losses.Reduction.SUM)
    ])
    labels = {"head1": tf.constant([0, 1]), "head2": tf.constant([0, 1])}
  else:
    head = tf.contrib.estimator.binary_classification_head(
        loss_reduction=tf.losses.Reduction.SUM)
    labels = tf.constant([0, 1])
  features = {"x": tf.constant([[1.], [2.]])}
  builder = _EnsembleBuilder(head, metric_fn=metric_fn)
  subnetwork_manager = _SubnetworkManager(head, metric_fn)
  subnetwork_builder = _Builder(
      lambda unused0, unused1: tf.no_op(),
      lambda unused0, unused1: tf.no_op(),
      use_logits_last_layer=True)

  subnetwork_spec = subnetwork_manager.build_subnetwork_spec(
      name="test",
      subnetwork_builder=subnetwork_builder,
      iteration_step=1,
      summary=_FakeSummary(),
      features=features,
      mode=mode,
      labels=labels)
  ensemble_spec = builder.build_ensemble_spec(
      name="test",
      candidate=EnsembleCandidate("foo", [subnetwork_builder], None),
      ensembler=ComplexityRegularizedEnsembler(
          mixture_weight_type=MixtureWeightType.SCALAR),
      subnetwork_specs=[subnetwork_spec],
      summary=_FakeSummary(),
      features=features,
      iteration_number=0,
      iteration_step=1,
      labels=labels,
      mode=mode)
  fn, kwargs = subnetwork_spec.eval_metrics
  subnetwork_metric_ops = fn(**kwargs)
  fn, kwargs = ensemble_spec.eval_metrics
  ensemble_metric_ops = fn(**kwargs)
  sess.run((tf.global_variables_initializer(),
            tf.local_variables_initializer()))
  subnetwork_metrics, ensemble_metrics = sess.run((subnetwork_metric_ops,
                                                   ensemble_metric_ops))
  return {k: subnetwork_metrics[k][1] for k in subnetwork_metrics}, {
      k: ensemble_metrics[k][1] for k in ensemble_metrics
  }


class EnsembleBuilderMetricFnTest(parameterized.TestCase, tf.test.TestCase):

  @parameterized.named_parameters({
      "testcase_name": "mode_train",
      "mode": tf.estimator.ModeKeys.TRAIN,
  }, {
      "testcase_name": "mode_predict",
      "mode": tf.estimator.ModeKeys.PREDICT,
  })
  def test_only_adds_metrics_when_evaluating(self, mode):
    """Ensures that metrics are only added during evaluation.

    Adding metrics during training will break when running on TPU.

    Args:
      mode: The mode with which to run the test.
    """

    def metric_fn(features):
      return {"mean_x": tf.metrics.mean(features["x"])}

    with self.test_session() as sess:
      subnetwork_metrics, ensemble_metrics = _make_metrics(
          sess, metric_fn, mode)

    self.assertEmpty(subnetwork_metrics)
    self.assertEmpty(ensemble_metrics)

  def test_should_add_metrics(self):

    def _test_metric_fn(metric_fn):
      with self.test_session() as sess:
        subnetwork_metrics, ensemble_metrics = _make_metrics(sess, metric_fn)
      self.assertIn("mean_x", subnetwork_metrics)
      self.assertIn("mean_x", ensemble_metrics)
      self.assertEqual(1.5, subnetwork_metrics["mean_x"])
      self.assertEqual(1.5, ensemble_metrics["mean_x"])
      # assert that it keeps original head metrics
      self.assertIn("auc", subnetwork_metrics)
      self.assertIn("auc", ensemble_metrics)

    def metric_fn_1(features):
      return {"mean_x": tf.metrics.mean(features["x"])}

    # TODO: Add support for tf.keras.metrics.Mean like `add_metrics`.
    _test_metric_fn(metric_fn_1)

  def test_should_error_out_for_not_recognized_args(self):
    head = tf.contrib.estimator.binary_classification_head(
        loss_reduction=tf.losses.Reduction.SUM)

    def metric_fn(features, not_recognized):
      _, _ = features, not_recognized
      return {}

    with self.assertRaisesRegexp(ValueError, "not_recognized"):
      _EnsembleBuilder(head, metric_fn=metric_fn)

  def test_all_supported_args(self):

    def metric_fn(features, predictions, labels):
      self.assertIn("x", features)
      self.assertIsNotNone(labels)
      self.assertIn("logistic", predictions)
      return {}

    with self.test_session() as sess:
      _make_metrics(sess, metric_fn)

  def test_all_supported_args_in_different_order(self):

    def metric_fn(labels, features, predictions):
      self.assertIn("x", features)
      self.assertIsNotNone(labels)
      self.assertIn("logistic", predictions)
      return {}

    with self.test_session() as sess:
      _make_metrics(sess, metric_fn)

  def test_all_args_are_optional(self):

    def _test_metric_fn(metric_fn):
      with self.test_session() as sess:
        subnetwork_metrics, ensemble_metrics = _make_metrics(sess, metric_fn)
      self.assertEqual(2., subnetwork_metrics["two"])
      self.assertEqual(2., ensemble_metrics["two"])

    def metric_fn_1():
      return {"two": tf.metrics.mean(tf.constant([2.]))}

    # TODO: Add support for tf.keras.metrics.Mean like `add_metrics`.
    _test_metric_fn(metric_fn_1)

  def test_overrides_existing_metrics(self):

    def _test_metric_fn(metric_fn):
      with self.test_session() as sess:
        subnetwork_metrics, ensemble_metrics = _make_metrics(
            sess, metric_fn=None)
      self.assertNotEqual(2., subnetwork_metrics["auc"])
      self.assertNotEqual(2., ensemble_metrics["auc"])

      with tf.Graph().as_default() as g, self.test_session(g) as sess:
        subnetwork_metrics, ensemble_metrics = _make_metrics(
            sess, metric_fn=metric_fn)
      self.assertEqual(2., subnetwork_metrics["auc"])
      self.assertEqual(2., ensemble_metrics["auc"])

    def metric_fn_1():
      return {"auc": tf.metrics.mean(tf.constant([2.]))}

    # TODO: Add support for tf.keras.metrics.Mean like `add_metrics`.
    _test_metric_fn(metric_fn_1)

  def test_multi_head(self):
    """Tests b/123084079."""

    def metric_fn(predictions):
      self.assertIn(("head1", "logits"), predictions)
      self.assertIn(("head2", "logits"), predictions)
      return {}

    with self.test_session() as sess:
      _make_metrics(sess, metric_fn, multi_head=True)


if __name__ == "__main__":
  tf.test.main()
