import autograd.numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spl
import copy

from autograd.extend import primitive, defvjp, defjvp

from ceviche.constants import *
from ceviche.utils import make_sparse, spdot
from ceviche.solvers import sparse_solve

class fdfd():
    """ Base class for FDFD simulation """

    def __init__(self, omega, dL, eps_r, npml):
        """ initialize with a given structure and source """

        self.omega = omega
        self.dL = dL
        self.npml = npml

        self.info_dict = {'omega': self.omega}

        self.shape = self.Nx, self.Ny = eps_r.shape

        self.eps_r = eps_r
        self.N = self.eps_vec.size

        self.setup_derivatives()
        self.A = self.make_A(self.eps_vec)

    def setup_derivatives(self):

        # Creates all of the operators needed for later
        info_dict = compute_derivative_matrices(self.omega, self.shape, self.npml, self.dL)
        self.Dxf, self.Dxb, self.Dyf, self.Dyb = info_dict

        # save to a dictionary for convenience passing to primitives
        self.info_dict['Dxf'] = self.Dxf
        self.info_dict['Dxb'] = self.Dxb
        self.info_dict['Dyf'] = self.Dyf
        self.info_dict['Dyb'] = self.Dyb

    @property
    def eps_r(self):
        """ Returns the relative permittivity grid """
        return self.__eps_r

    @eps_r.setter
    def eps_r(self, new_eps):
        """ Defines some attributes when eps_r is set. """
        if new_eps.shape != self.shape:
            self.__init__(self.omega, self.dL, new_eps, npml)
        else:
            self.__eps_r = new_eps
            self.eps_vec = self.__eps_r.flatten()

    def make_A(self, eps_r):
        raise NotImplementedError("need to make a make_A() method")

    def solve_fn(self, eps_vec, source_vec):
        raise NotImplementedError("need to implement a solve function")

    def z_to_xy(self, Fz_vec, eps_vec):
        raise NotImplementedError("need to implement a z -> {x, y} field conversion function")

    def solve(self, source):
        """ Generic solve function """

        # make source a vector
        source_vec = source.flatten()

        # solve the z component of the fields
        Fz_vec = self.solve_fn(self.eps_vec, source_vec)

        # get the x and y vecays, put into tuple
        Fx_vec, Fy_vec = self.z_to_xy(Fz_vec, self.eps_vec)
        field_vecays = (Fx_vec, Fy_vec, Fz_vec)

        # convert all fields to grid and return tuple of them
        Fs = map(self._vec_to_grid, field_vecays)
        return tuple(Fs)

    def _vec_to_grid(self, vec):
        return np.reshape(vec, self.shape)

""" These are the fdfd classes that you'll actually want to use """

class fdfd_hz(fdfd):
    """ FDFD class for Hz polarization """

    def __init__(self, omega, L0, eps_r, npml):
        super().__init__(omega, L0, eps_r, npml)

    def make_A(self, eps_vec):
        return make_A_Hz(self.info_dict, eps_vec)

    def solve_fn(self, eps_vec, source_vec):
        return solve_Hz(self.info_dict, eps_vec, source_vec)

    def z_to_xy(self, Fz_vec, eps_vec):
        return H_to_E(Fz_vec, self.info_dict, eps_vec)

class fdfd_ez(fdfd):
    """ FDFD class for Ez polarization """

    def __init__(self, omega, L0, eps_r, npml):
        super().__init__(omega, L0, eps_r, npml)

    def make_A(self, eps_vec):
        return make_A_Ez(self.info_dict, eps_vec)

    def solve_fn(self, eps_vec, source_vec):
        return solve_Ez(self.info_dict, eps_vec, source_vec)

    def z_to_xy(self, Fz_vec, eps_vec):
        return E_to_H(Fz_vec, self.info_dict, None)


""" This section is the meat and bones of the FDFD.
    It defines the basic operations needed for FDFD and also their derivatives
    in a form that autograd can understand.
    This allows you to use fdfd classes in autograd functions.
    Look but don't touch!

    NOTES for the curious (since this information isnt in autograd documentation...)

        To define a function as being trackable by autograd, need to add the 
        @primitive decorator

    REVERSE MODE
        'vjp' defines the vector-jacobian product for reverse mode (adjoint)
        a vjp_maker function takes as arguments
            1. the output of the @primitive
            2. the rest of the original arguments in the @primitive
        and returns
            a *function* of the backprop vector (v) that defines the operation
            (d{function} / d{argument_i})^T @ v

    FORWARD MODE:
        'jvp' defines the jacobian-vector product for forward mode (FMD)
        a jvp_maker function takes as arguments
            1. the forward propagating vector (g)
            2. the rest of the original arguments in the @primitive
        and returns
            (d{function} / d{argument_i}) @ g

    After this, you need to link the @primitive to its vjp/jvp using
    defvjp(function, arg1's vjp, arg2's vjp, ...)
    defjvp(function, arg1's jvp, arg2's jvp, ...)
"""

"""======================== SYSTEM MATRIX CREATION ========================"""

def make_A_Hz(info_dict, eps_vec):
    """ constructs the system matrix for `Hz` polarization """
    diag = 1 / EPSILON_0 * sp.spdiags(1/eps_vec, [0], eps_vec.size, eps_vec.size)
    A = spdot(info_dict['Dxf'], spdot(info_dict['Dxb'].T, diag).T) \
      + spdot(info_dict['Dyf'], spdot(info_dict['Dyb'].T, diag).T) \
      + info_dict['omega']**2 * MU_0 * sp.eye(eps_vec.size)
    return A

def make_A_Ez(info_dict, eps_vec):
    """ constructs the system matrix for `Ez` polarization """
    diag = EPSILON_0 * sp.spdiags(eps_vec, [0], eps_vec.size, eps_vec.size)
    A = 1 / MU_0 * info_dict['Dxf'].dot(info_dict['Dxb']) \
      + 1 / MU_0 * info_dict['Dyf'].dot(info_dict['Dyb']) \
      + info_dict['omega']**2 * diag
    return A

"""========================== FIELD CONVERSIONS ==========================="""

def Ez_to_Hx(Ez, info_dict):
    """ Returns magnetic field `Hx` from electric field `Ez` """
    Hx = - spdot(info_dict['Dyb'], Ez) / MU_0
    return Hx

def Ez_to_Hy(Ez, info_dict):
    """ Returns magnetic field `Hy` from electric field `Ez` """
    Hy =  spdot(info_dict['Dxb'], Ez) / MU_0
    return Hy

def E_to_H(Ez, info_dict, eps_vec=None):
    """ More convenient function to return both Hx and Hy from Ez """
    Hx = Ez_to_Hx(Ez, info_dict)
    Hy = Ez_to_Hy(Ez, info_dict)
    return Hx, Hy

def Hz_to_Ex(Hz, info_dict, eps_vec, adjoint=False):
    """ Returns electric field `Ex` from magnetic field `Hz` """
    # note: adjoint switch is because backprop thru this fn. has different form
    if adjoint:
        Ex =  spdot(info_dict['Dyf'].T, Hz) / eps_vec / EPSILON_0
    else:
        Ex = -spdot(info_dict['Dyb'],   Hz) / eps_vec / EPSILON_0
    return Ex

def Hz_to_Ey(Hz, info_dict, eps_vec, adjoint=False):
    """ Returns electric field `Ey` from magnetic field `Hz` """
    if adjoint:
        Ey = -spdot(info_dict['Dxf'].T, Hz) / eps_vec / EPSILON_0
    else:
        Ey =  spdot(info_dict['Dxb'],   Hz) / eps_vec / EPSILON_0
    return Ey

def H_to_E(Hz, info_dict, eps_vec, adjoint=False):
    """ More convenient function to return both Ex and Ey from Hz """
    Ex = Hz_to_Ex(Hz, info_dict, eps_vec, adjoint=adjoint)
    Ey = Hz_to_Ey(Hz, info_dict, eps_vec, adjoint=adjoint)
    return Ex, Ey

"""======================== SOLVING FOR THE FIELDS ========================"""

@primitive
def solve_Ez(info_dict, eps_vec, source):
    """ solve `Ez = A^-1 b` where A is constructed from the FDFD `info_dict`
        and 'eps_vec' is a (1D) vecay of the relative permittivity
    """
    A = make_A_Ez(info_dict, eps_vec)
    b = 1j * info_dict['omega'] * source
    Ez = sparse_solve(A, b)
    return Ez

# define the gradient of solve_Ez w.r.t. eps_vec (in Ez)
def vjp_maker_solve_Ez(Ez, info_dict, eps_vec, source):
    """ Gives vjp for solve_Ez with respect to eps_vec """    
    # construct the system matrix again
    A = make_A_Ez(info_dict, eps_vec)
    # vector-jacobian product function to return
    def vjp(v):
        # solve the adjoint problem and get those electric fields (note D info_dict are different and transposed)
        Ez_aj = sparse_solve(A.T, -v)
        # because we care about the diagonal elements, just element-wise multiply E and E_adj
        # note: need np.real() for adjoint returns w.r.t. real quantities but not in forward mode
        return EPSILON_0 * info_dict['omega']**2 * np.real(Ez_aj * Ez)
    return vjp

def vjp_maker_solve_Ez_source(Ez, info_dict, eps_vec, source):
    """ Gives vjp for solve_Ez with respect to source """    
    A = make_A_Ez(info_dict, eps_vec)
    def vjp(v):
        return 1j * info_dict['omega'] * sparse_solve(A.T, v)
    return vjp

# define the gradient of solve_Ez w.r.t. eps_vec (in Ez)
def jvp_solve_Ez(g, Ez, info_dict, eps_vec, source):
    """ Gives jvp for solve_Ez with respect to eps_vec """    
    # construct the system matrix again and the RHS of the gradient expersion
    A = make_A_Ez(info_dict, eps_vec)
    u = Ez * -g
    # solve the adjoint problem and get those electric fields (note D info_dict are different and transposed)
    Ez_for = sparse_solve(A, u)
    # because we care about the diagonal elements, just element-wise multiply E and E_adj
    return EPSILON_0 * info_dict['omega']**2 * Ez_for

def jvp_solve_Ez_source(g, Ez, info_dict, eps_vec, source):
    """ Gives jvp for solve_Ez with respect to source """  
    A = make_A_Ez(info_dict, eps_vec)      
    return 1j * info_dict['omega'] * sparse_solve(A, g)

defvjp(solve_Ez, None, vjp_maker_solve_Ez, vjp_maker_solve_Ez_source)
defjvp(solve_Ez, None, jvp_solve_Ez, jvp_solve_Ez_source)

@primitive
def solve_Hz(info_dict, eps_vec, source):
    """ solve `Hz = A^-1 b` where A is constructed from the FDFD `info_dict`
        and 'eps_vec' is a (1D) vecay of the relative permittivity
    """
    A = make_A_Hz(info_dict, eps_vec)
    b = 1j * info_dict['omega'] * source    
    Hz = sparse_solve(A, b)
    return Hz

def vjp_maker_solve_Hz(Hz, info_dict, eps_vec, source):
    """ Gives vjp for solve_Hz with respect to eps_vec """    
    # get the forward electric fields
    Ex, Ey = H_to_E(Hz, info_dict, eps_vec, adjoint=False)
    # construct the system matrix again
    A = make_A_Hz(info_dict, eps_vec)
    # vector-jacobian product function to return
    def vjp(v):
        # solve the adjoint problem and get those electric fields (note D info_dict are different and transposed)
        Hz_aj = sparse_solve(A.T, -v)
        Ex_aj, Ey_aj = H_to_E(Hz_aj, info_dict, eps_vec, adjoint=True)
        # because we care about the diagonal elements, just element-wise multiply E and E_adj
        return EPSILON_0 * np.real(Ex_aj * Ex + Ey_aj * Ey)
    # return this function for autograd to link-later
    return vjp

def vjp_maker_solve_Hz_source(Hz, info_dict, eps_vec, source):
    """ Gives vjp for solve_Hz with respect to source """    
    A = make_A_Hz(info_dict, eps_vec)
    def vjp(v):
        return 1j * info_dict['omega'] * sparse_solve(A.T, v)
    return vjp

# define the gradient of solve_Hz w.r.t. eps_vec (in Hz)
def jvp_solve_Hz(g, Hz, info_dict, eps_vec, source):
    """ Gives jvp for solve_Hz with respect to eps_vec """    
    # construct the system matrix again and the RHS of the gradient expersion
    A = make_A_Hz(info_dict, eps_vec)
    ux = spdot(info_dict['Dxb'], Hz)
    uy = spdot(info_dict['Dyb'], Hz)
    diag = sp.spdiags(1 / eps_vec, [0], eps_vec.size, eps_vec.size)
    # the g gets multiplied in at the middle of the expression
    ux = ux * diag * g * diag
    uy = uy * diag * g * diag
    ux = spdot(info_dict['Dxf'], ux)
    uy = spdot(info_dict['Dyf'], uy)
    # add the x and y components and multiply by A_inv on the left
    u = (ux + uy)
    Hz_for = sparse_solve(A, u)
    return 1 / EPSILON_0 * Hz_for

def jvp_solve_Hz_source(g, Hz, info_dict, eps_vec, source):
    """ Gives jvp for solve_Hz with respect to source """    
    A = make_A_Hz(info_dict, eps_vec)      
    return 1j * info_dict['omega'] * sparse_solve(A, g)

defvjp(solve_Hz, None, vjp_maker_solve_Hz, vjp_maker_solve_Hz_source)
defjvp(solve_Hz, None, jvp_solve_Hz, jvp_solve_Hz_source)


"""=========================== HELPER FUNCTIONS ==========================="""

def compute_derivative_matrices(omega, shape, npml, dL):

    # make the S-matrices for PML
    (Sxf, Sxb, Syf, Syb) = S_create(omega, shape, npml, dL)

    # Construct derivate matrices without PML
    Dxf_0 = createDws('x', 'f', dL, shape)
    Dxb_0 = createDws('x', 'b', dL, shape)
    Dyf_0 = createDws('y', 'f', dL, shape)
    Dyb_0 = createDws('y', 'b', dL, shape)

    # apply PML to derivative matrices
    Dxf = Sxf.dot(Dxf_0)
    Dxb = Sxb.dot(Dxb_0)
    Dyf = Syf.dot(Dyf_0)
    Dyb = Syb.dot(Dyb_0)

    return Dxf, Dxb, Dyf, Dyb


def S_create(omega, shape, npml, dL):
    # creates S matrices for the PML creation

    Nx, Ny = shape
    N = Nx * Ny
    x_range = [0, float(dL * Nx)]
    y_range = [0, float(dL * Ny)]

    Nx_pml, Ny_pml = npml    

    # Create the sfactor in each direction and for 'f' and 'b'
    s_vector_x_f = create_sfactor('f', omega, dL, Nx, Nx_pml)
    s_vector_x_b = create_sfactor('b', omega, dL, Nx, Nx_pml)
    s_vector_y_f = create_sfactor('f', omega, dL, Ny, Ny_pml)
    s_vector_y_b = create_sfactor('b', omega, dL, Ny, Ny_pml)

    # Fill the 2D space with layers of appropriate s-factors
    Sx_f_2D = np.zeros(shape, dtype=np.complex128)
    Sx_b_2D = np.zeros(shape, dtype=np.complex128)
    Sy_f_2D = np.zeros(shape, dtype=np.complex128)
    Sy_b_2D = np.zeros(shape, dtype=np.complex128)

    for i in range(0, Ny):
        Sx_f_2D[:, i] = 1 / s_vector_x_f
        Sx_b_2D[:, i] = 1 / s_vector_x_b

    for i in range(0, Nx):
        Sy_f_2D[i, :] = 1 / s_vector_y_f
        Sy_b_2D[i, :] = 1 / s_vector_y_b

    # Reshape the 2D s-factors into a 1D s-vecay
    Sx_f_vec = Sx_f_2D.reshape((-1,))
    Sx_b_vec = Sx_b_2D.reshape((-1,))
    Sy_f_vec = Sy_f_2D.reshape((-1,))
    Sy_b_vec = Sy_b_2D.reshape((-1,))

    # Construct the 1D total s-vecay into a diagonal matrix
    Sx_f = sp.spdiags(Sx_f_vec, 0, N, N)
    Sx_b = sp.spdiags(Sx_b_vec, 0, N, N)
    Sy_f = sp.spdiags(Sy_f_vec, 0, N, N)
    Sy_b = sp.spdiags(Sy_b_vec, 0, N, N)

    return Sx_f, Sx_b, Sy_f, Sy_b


def createDws(w, s, dL, shape):
    """ creates the derivative matrices
            NOTE: python uses C ordering rather than Fortran ordering. Therefore the
            derivative operators are constructed slightly differently than in MATLAB
    """

    Nx, Ny = shape

    if w is 'x':
        if Nx > 1:
            if s is 'f':
                dxf = sp.diags([-1, 1, 1], [0, 1, -Nx+1], shape=(Nx, Nx))
                Dws = 1 / dL * sp.kron(dxf, sp.eye(Ny))
            else:
                dxb = sp.diags([1, -1, -1], [0, -1, Nx-1], shape=(Nx, Nx))
                Dws = 1 / dL * sp.kron(dxb, sp.eye(Ny))
        else:
            Dws = sp.eye(Ny)            
    if w is 'y':
        if Ny > 1:
            if s is 'f':
                dyf = sp.diags([-1, 1, 1], [0, 1, -Ny+1], shape=(Ny, Ny))
                Dws = 1 / dL * sp.kron(sp.eye(Nx), dyf)
            else:
                dyb = sp.diags([1, -1, -1], [0, -1, Ny-1], shape=(Ny, Ny))
                Dws = 1 / dL * sp.kron(sp.eye(Nx), dyb)
        else:
            Dws = sp.eye(Nx)
    return Dws


def sig_w(l, dw, m=3, lnR=-30):
    # helper for S()
    sig_max = -(m + 1) * lnR / (2 * ETA_0 * dw)
    return sig_max * (l / dw)**m


def S(l, dw, omega):
    # helper for create_sfactor()
    return 1 - 1j * sig_w(l, dw) / (omega * EPSILON_0)


def create_sfactor(s, omega, dL, N, N_pml):
    # used to help construct the S matrices for the PML creation

    sfactor_vecay = np.ones(N, dtype=np.complex128)
    if N_pml < 1:
        return sfactor_vecay

    dw = N_pml * dL

    for i in range(N):
        if s is 'f':
            if i <= N_pml:
                sfactor_vecay[i] = S(dL * (N_pml - i + 0.5), dw, omega)
            elif i > N - N_pml:
                sfactor_vecay[i] = S(dL * (i - (N - N_pml) - 0.5), dw, omega)
        if s is 'b':
            if i <= N_pml:
                sfactor_vecay[i] = S(dL * (N_pml - i + 1), dw, omega)
            elif i > N - N_pml:
                sfactor_vecay[i] = S(dL * (i - (N - N_pml) - 1), dw, omega)
    return sfactor_vecay
