"""
quantum circuit: state simulator
"""

from typing import Tuple, List, Callable, Optional, Any, Sequence
from functools import reduce

import numpy as np
from .mps_base import MyFiniteMPS

from . import gates
from .cons import backend, contractor, dtypestr, npdtype # have to be imported even if not used!

Gate = gates.Gate
Tensor = Any


def split_tensor(tensor: Tensor,
                 left=True,
                 max_singular_values=None,
                 max_truncation_err=None,
                 relative=True,
                 ) -> Tuple[Tensor, Tensor]:
    """
    Split the tensor by SVD or QR depends on whether a truncation is required

    :param tensor: The input tensor to split.
    :type tensor: Tensor
    :param left: Determine the orthogonal center is on the left tensor or the right tensor.
    :type left: bool
    :param max_singular_values: The maximum number of singular values to keep.
    :type max_singular_values: Optional[int]
    :param max_truncation_err: The maximum allowed truncation error.
    :type max_truncation_err: Optional[float]
    :param relative: Multiply `max_truncation_err` with the largest singular value.
    :type relative: bool
    """
    svd = (max_truncation_err is not None) or (max_singular_values is not None)
    if svd:
        U, S, VH, _ = backend.svd(tensor, max_singular_values=max_singular_values, max_truncation_error=max_truncation_err, relative=relative)
        if left:
            return backend.matmul(U, backend.diagflat(S)), VH
        else:
            return U, backend.matmul(backend.diagflat(S), VH)
    else:
        if left:
            return backend.rq(tensor)
        else:
            return backend.qr(tensor)


class MPSCircuit:
    """
    ``MPSCircuit`` class.
    Simple usage demo below.

    .. code-block:: python

        mps = tc.MPSCircuit(3)
        mps.H(1)
        mps.CNOT(0, 1)
        mps.rx(2, theta=tc.num_to_tensor(1.))
        mps.expectation([tc.gates.z(), (2, )]) # 0.54

    """

    sgates = (
        ["i", "x", "y", "z", "h", "t", "s", "rs", "wroot"]
        + ["cnot", "cz", "swap", "cy"]
    )
    # gates on > 2 qubits like toffoli is not available
    # however they can be constructed from 1 and 2 qubit gates
    vgates = ["r", "cr", "rx", "ry", "rz", "any", "exp", "exp1"]

    def __init__(
        self,
        nqubits: int,
        tensors: Optional[Sequence[Tensor]] = None,
        center_position: int = 0,
    ) -> None:
        """
        MPSCircuit object based on state simulator.

        :param nqubits: The number of qubits in the circuit.
        :type nqubits: int
        :param tensors: If not None, the initial state of the circuit is taken as ``tensors``
            instead of :math:`\\vert 0\\rangle^n` qubits, defaults to None
        :type tensors: Optional[Tensor], optional
        :param center_position: the center position of MPS, default to 0
        :type center_position: int
        """
        if tensors is None:
            tensors = [np.array([1.0, 0.0], dtype=npdtype)[None, :, None] for i in range(nqubits)]
        else:
            assert len(tensors) == nqubits

        self._mps = MyFiniteMPS(tensors, canonicalize=True, center_position=center_position)
        self._nqubits = nqubits
        self._fidelity = 1.0
        self.set_truncation_rule()

    # `MPSCircuit` does not has `replace_inputs` like `Circuit` because the gates are immediately absorted into the MPS when applied, so it is impossible to remember the initial structure

    def set_truncation_rule(self,
                            max_singular_values: Optional[int] = None,
                            max_truncation_err: Optional[float] = None,
                            relative: bool = False,
                            ) -> None:
        """
        Set truncation rules when double qubit gates are applied.
        If nothing is specified, no truncation will take place and the bond dimension will keep growing.
        For more details, refer to `split_tensor`

        :param max_singular_values: The maximum number of singular values to keep.
        :type max_singular_values: Optional[int]
        :param max_truncation_err: The maximum allowed truncation error.
        :type max_truncation_err: Optional[float]
        :param relative: Multiply `max_truncation_err` with the largest singular value.
        :type relative: bool
        """
        self.max_singular_values = max_singular_values
        self.max_truncation_err = max_truncation_err
        self.relative = relative
        self.double_gate_compression_options = {
            'max_singular_values': max_singular_values,
            'max_truncation_err': max_truncation_err,
            'relative': relative,
        }
        self.do_truncation = (self.max_truncation_err is not None) or (self.max_truncation_err is not None)

    def position(self, site: int):
        """
        Wrapper of tn.FiniteMPS.position
        Set orthogonality center
        """
        self._mps.position(site, normalize=False)

    @ classmethod
    def _meta_apply(cls) -> None:

        for g in cls.sgates:
            setattr(
                cls, g, cls.apply_general_gate_delayed(gatef=getattr(gates, g))
            )
            setattr(
                cls,
                g.upper(),
                cls.apply_general_gate_delayed(gatef=getattr(gates, g)),
            )
            matrix = gates.matrix_for_gate(getattr(gates, g)())
            matrix = gates.bmatrix(matrix)
            doc = """
            Apply %s gate on the circuit.

            :param index: Qubit number than the gate applies on.
                The matrix for the gate is

                .. math::

                      %s

            :type index: int.
            """ % (
                g,
                matrix,
            )
            docs = """
            Apply %s gate on the circuit.

            :param index: Qubit number than the gate applies on.
            :type index: int.
            """ % (
                g
            )
            if g in ["rs"]:
                getattr(cls, g).__doc__ = docs
                getattr(cls, g.upper()).__doc__ = docs

            else:
                getattr(cls, g).__doc__ = doc
                getattr(cls, g.upper()).__doc__ = doc

        for g in cls.vgates:
            setattr(
                cls,
                g,
                cls.apply_general_variable_gate_delayed(
                    gatef=getattr(gates, g)
                ),
            )
            setattr(
                cls,
                g.upper(),
                cls.apply_general_variable_gate_delayed(
                    gatef=getattr(gates, g)
                ),
            )
            doc = """
            Apply %s gate with parameters on the circuit.

            :param index: Qubit number than the gate applies on.
            :type index: int.
            :param vars: Parameters for the gate
            :type vars: float.
            """ % (
                g
            )
            getattr(cls, g).__doc__ = doc
            getattr(cls, g.upper()).__doc__ = doc

    def apply_single_gate(self, gate: Gate, index: int) -> None:
        """
        Apply a single qubit gate on MPS, the gate must be unitary, no truncation is needed
        """
        self._mps.apply_one_site_gate(gate.tensor, index)

    def apply_adjacent_double_gate(self,
                                   gate: Gate,
                                   index1: int,
                                   index2: int,
                                   center_position: Optional[int] = None,
                                   ) -> None:
        """
        Apply a double qubit gate on adjacent qubits of MPS, truncation rule is speficied by `set_truncation_rule`
        """
        # The center position of MPS must be either `index1` for `index2` before applying a double gate
        # Choose the one closer to the current center
        assert index2 - index1 == 1
        diff1 = abs(index1 - self._mps.center_position)
        diff2 = abs(index2 - self._mps.center_position)
        if diff1 < diff2:
            self.position(index1)
        else:
            self.position(index2)
        err = self._mps.apply_two_site_gate(gate.tensor, index1, index2, center_position=center_position, **self.double_gate_compression_options)
        self._fidelity *= 1 - backend.real(backend.sum(err**2))

    def apply_double_gate(self,
                          gate: Gate,
                          index1: int,
                          index2: int,
                          ) -> None:
        """
        Apply a double qubit gate on MPS, truncation rule is speficied by `set_truncation_rule`
        """
        # Equivalent to apply N SWPA gates, the required gate, N SWAP gates sequentially on adjacent gates
        diff1 = abs(index1 - self._mps.center_position)
        diff2 = abs(index2 - self._mps.center_position)
        if diff1 < diff2:
            self.position(index1)
            for index in np.arange(index1, index2 - 1):
                self.apply_adjacent_double_gate(gates.swap(), index, index + 1, center_position=index + 1)
            self.apply_adjacent_double_gate(gate, index2 - 1, index2, center_position=index2 - 1)
            for index in np.arange(index1, index2 - 1)[::-1]:
                self.apply_adjacent_double_gate(gates.swap(), index, index + 1, center_position=index)
        else:
            self.position(index2)
            for index in np.arange(index1 + 1, index2)[::-1]:
                self.apply_adjacent_double_gate(gates.swap(), index, index + 1, center_position=index)
            self.apply_adjacent_double_gate(gate, index1, index1 + 1, center_position=index1 + 1)
            for index in np.arange(index1 + 1, index2):
                self.apply_adjacent_double_gate(gates.swap(), index, index + 1, center_position=index + 1)

    def apply_general_gate(
        self, gate: Gate, *index: int
    ) -> None:
        assert len(index) == len(set(index))
        noe = len(index)
        if noe == 1:
            self.apply_single_gate(gate, *index)
        elif noe == 2:
            self.apply_double_gate(gate, *index)
        else:
            raise ValueError("MPS does not support application of gate on > 2 qubits")

    apply = apply_general_gate

    @ staticmethod
    def apply_general_gate_delayed(
        gatef: Callable[[], Gate]
    ) -> Callable[..., None]:
        # nested function must be utilized, functools.partial doesn't work for method register on class
        # see https://re-ra.xyz/Python-中实例方法动态绑定的几组最小对立/
        def apply(self: "MPSCircuit", *index: int) -> None:
            gate = gatef()
            self.apply_general_gate(gate, *index)

        return apply

    @ staticmethod
    def apply_general_variable_gate_delayed(
        gatef: Callable[..., Gate],
    ) -> Callable[..., None]:
        def apply(self: "MPSCircuit", *index: int, **vars: float) -> None:
            gate = gatef(**vars)
            self.apply_general_gate(gate, *index)

        return apply

    def mid_measurement(self, index: int, keep: int = 0) -> None:
        """
        middle measurement in z basis on the circuit, note the wavefunction output is not normalized
        with ``mid_measurement`` involved, one should normalized the state manually if needed.

        :param index: the index of qubit that the Z direction postselection applied on
        :type index: int
        :param keep: 0 for spin up, 1 for spin down, defaults to 0
        :type keep: int, optional
        """
        # normalization not guaranteed
        assert keep in [0, 1]
        self.position(index)
        self._mps.tensors[index] = self._mps.tensors[index][:, keep, :]

    def is_valid(self) -> bool:
        """
        check whether the circuit is legal

        :return:
        """
        try:
            mps = self._mps
            if len(mps) != self._nqubits:
                return False
            for i in range(self._nqubits):
                if len(mps.tensors[i].shape) != 3:
                    return False
            for i in range(self._nqubits - 1):
                if mps.tensors[i].shape[-1] != mps.tensors[i + 1].shape[0]:
                    return False
            return True
        except BaseException:
            return False

    @staticmethod
    def from_wavefunction(wavefunction: Tensor, max_singular_values=None, max_truncation_err=None, relative=True) -> "MPSCircuit":
        """
        compute the output wavefunction from the circuit

        :return: Tensor with shape [-1, 1]
        :rtype: Tensor
        """
        wavefunction = wavefunction.reshape((-1, 1))
        tensors: List[Tensor] = []
        while True:
            nright = wavefunction.shape[1]
            wavefunction = wavefunction.reshape((-1, nright * 2))
            wavefunction, Q = split_tensor(wavefunction, left=True, max_singular_values=max_singular_values, max_truncation_err=max_truncation_err, relative=relative)
            tensors.insert(0, Q.reshape((-1, 2, nright)))
            if wavefunction.shape == (1, 1):
                break
        return MPSCircuit(len(tensors), tensors=tensors)

    def wavefunction(self) -> Tensor:
        """
        compute the output wavefunction from the circuit

        :return: Tensor with shape [-1, 1]
        :rtype: Tensor
        """
        result = backend.ones((1, 1, 1), dtype=npdtype)
        for tensor in self._mps.tensors:
            result = backend.einsum("iaj,jbk->iabk", [result, tensor])
            ni, na, nb, nk = result.shape
            result = backend.reshape(result, (ni, na * nb, nk))
        return backend.reshape(result, [1, -1])

    state = wavefunction

    def copy(self) -> "MPSCircuit":
        tensor = [t.copy() for t in self._mps.tensors]
        result = MPSCircuit(self._nqubits, tensor, center_position=self._mps.center_position)
        result.set_truncation_rule(max_singular_values=self.max_singular_values, max_truncation_err=self.max_truncation_err, relative=self.relative)
        return result

    def conj(self) -> "MPSCircuit":
        tensor = [t.conj() for t in self._mps.tensors]
        result = MPSCircuit(self._nqubits, tensor, center_position=self._mps.center_position)
        result.set_truncation_rule(max_singular_values=self.max_singular_values, max_truncation_err=self.max_truncation_err, relative=self.relative)
        return result

    def get_norm(self) -> float:
        return self._mps.norm(self._mps.center_position)

    def normalize(self) -> None:
        center = self._mps.center_position
        norm = self._mps.norm(center)
        self._mps.tensor[center] /= norm

    def amplitude(self, l: str) -> Tensor:
        assert len(l) == self._nqubits
        tensors = [self._mps.tensors[i][:, int(s), :] for i, s in enumerate(l)]
        return reduce(backend.matmul, tensors)[0, 0]

    def measure(self, *index: int, with_prob: bool = False) -> Tuple[str, float]:
        """
        :param index: measure on which quantum line
        :param with_prob: if true, theoretical probability is also returned
        :return:
        """
        n = len(index)
        if not np.all(np.diff(index) >= 0):
            argsort = np.argsort(index)
            invargsort = np.zeros((n, ), dtype=int)
            invargsort[argsort] = np.arange(n)
            sample, prob = self.measure(*np.array(index)[argsort], with_prob=with_prob)
            return ''.join(np.array(list(sample))[invargsort]), prob

        # Assume no equivalent indices
        assert np.all(np.diff(index) > 0)
        # Assume that the index is in correct order
        mpscircuit = self.copy()
        sample = ""
        p = 1.0
        # TODO: add the possibility to move from right to left
        for i in index:
            # Move the center position to each index from left to right
            mpscircuit.position(i)
            tensor = mpscircuit._mps.tensors[i]
            probs = backend.sum(backend.power(backend.abs(tensor), 2), axis=(0, 2))
            # TODO: normalize the tensor to avoid error accumulation
            probs /= backend.sum(probs)
            pu = probs[0]
            r = backend.random_uniform([])
            if r < pu:
                choice = 0
            else:
                choice = 1
            sample += str(choice)
            p *= probs[choice]
            tensor = tensor[:, choice, :][:, None, :]
            mpscircuit._mps.tensors[i] = tensor
        if with_prob:
            return sample, p
        else:
            return sample, -1

    def proj_with_mps(self, other: "MPSCircuit") -> Tensor:
        """
        compute the projection between `other` as bra and `self` as ket

        :param ops: operator and its position on the circuit,
            eg. ``(gates.Z(), [1]), (gates.X(), [2])`` is for operator :math:`Z_1X_2`
        :type ops: Tuple[tn.Node, List[int]]
        """
        bra = other.conj().copy()
        ket = self.copy()
        assert bra._nqubits == ket._nqubits
        n = bra._nqubits
        while n > 1:
            # --bA---bB
            #   |    |
            #   |    |
            # --kA---kB
            bra_A, bra_B = bra._mps.tensors[-2:]
            ket_A, ket_B = ket._mps.tensors[-2:]
            proj_B = backend.einsum("iak,jak->ij", [bra_B, ket_B])
            new_kA = backend.einsum("iak,jk->iaj", [ket_A, proj_B])
            bra._mps.tensors = bra._mps.tensors[:-1]
            ket._mps.tensors = ket._mps.tensors[:-1]
            ket._mps.tensors[-1] = new_kA
            n -= 1
        bra_A = bra._mps.tensors[0]
        ket_A = ket._mps.tensors[0]
        result = backend.sum(bra_A * ket_A)
        return backend.convert_to_tensor(result)

    def general_expectation(
        self, *ops: Tuple[Gate, List[int]]
    ) -> Tensor:
        """
        compute expectation of corresponding operators

        :param ops: operator and its position on the circuit,
            eg. ``(gates.Z(), [1]), (gates.X(), [2])`` is for operator :math:`Z_1X_2`
        :type ops: Tuple[tn.Node, List[int]]
        """
        # A better idea is to create a MPO class and have a function to transform gates to MPO
        mpscircuit = self.copy()
        for gate, index in ops:
            mpscircuit.apply_general_gate(gate, *index)
        value = mpscircuit.proj_with_mps(self)
        return backend.convert_to_tensor(value)

    def expectation_single_gate(
        self, gate: Gate, site: int,
    ) -> Tensor:
        value = self._mps.measure_local_operator([gate.tensor], [site])[0]
        return backend.convert_to_tensor(value)

    def expectation_two_gates_correlations(
        self, gate1: Gate, gate2: Gate, site1: int, site2: int
    ) -> Tensor:
        value = self._mps.measure_two_body_correlator(gate1.tensor, gate2.tensor, site1, [site2])[0]
        return backend.convert_to_tensor(value)


MPSCircuit._meta_apply()